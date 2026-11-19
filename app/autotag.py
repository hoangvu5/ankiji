"""Tag notes that are already in Anki, using only tags from the user's own list.

autotag_cache.json remembers the AI's tags per note, keyed by note ID + the note's modified
time, so running again only sends new or edited notes to the AI.
"""
import hashlib
import html
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import anki, llm
from .paths import data

CACHE_PATH = data("autotag_cache.json")
BATCH = 30           # notes per AI call
RETRIES = 2          # extra tries for notes whose answer broke the rules
FIELD_CHARS = 400    # per field sent to the AI; the start of a card is enough to tag it
INFO_CHUNK = 500     # note IDs per notesInfo call
APPLY_CHUNK = 100    # actions per AnkiConnect multi call
ENUM_LIMIT = 500     # bigger lists go without a schema enum (OpenAI caps enum size); answers are still checked
MODES = ("overwrite", "add", "skip")

_lock = threading.Lock()  # guards the cache file and JOB
JOB = {"running": False, "phase": "", "done": 0, "total": 0, "result": None, "mode": "", "rows": [], "summary": None}
_current = {"job": None}  # llm.Job of the running tagging, so it can be cancelled


class TagError(Exception):
    pass


# ---------- input ----------
def clean_tags(raw):
    """User's tag list -> unique Anki tags. Anki tags can't hold spaces, so they become _."""
    items = raw if isinstance(raw, list) else re.split(r"[\n,]", str(raw or ""))
    out, seen = [], set()
    for t in items:
        t = re.sub(r"\s+", "_", str(t).strip())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def check_options(tags, mn, mx):
    try:
        mn, mx = int(mn), int(mx)
    except (TypeError, ValueError):
        raise TagError("Tags per card must be whole numbers.")
    if not tags:
        raise TagError("Add at least one tag to the list.")
    if mn < 0 or mx < 1:
        raise TagError("Tags per card: the minimum can't be below 0 and the maximum must be at least 1.")
    if mn > mx:
        raise TagError("Tags per card: the minimum can't be bigger than the maximum.")
    if mx > len(tags):
        raise TagError(f"Tags per card: the maximum can't be more than the {len(tags)} tag(s) in your list.")
    return mn, mx


# ---------- reading notes ----------
def _deck_query(name):
    # Anki search treats * and _ as wildcards and " ends the name, so escape them.
    return 'deck:"' + re.sub(r'([\\"*_])', r"\\\1", name) + '"'


def _plain(value):
    v = re.sub(r"\[sound:[^\]]*\]", " ", value or "")
    v = re.sub(r"<br\s*/?>|</div>|</p>", " ", v, flags=re.I)
    v = html.unescape(re.sub(r"<[^>]+>", "", v))
    v = re.sub(r"\s+", " ", v).strip()
    return v[:FIELD_CHARS] + ("..." if len(v) > FIELD_CHARS else "")


def _version(info):
    """Changes whenever the note is edited. Older AnkiConnect has no "mod", then the content decides."""
    if info.get("mod"):
        return str(info["mod"])
    body = json.dumps([info.get("fields"), info.get("tags")], sort_keys=True, ensure_ascii=False)
    return "h" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def _note(info):
    fields = sorted((info.get("fields") or {}).items(), key=lambda kv: kv[1].get("order", 0))
    vals = [_plain(v.get("value", "")) for _, v in fields]
    return {"id": info["noteId"], "ver": _version(info), "model": info.get("modelName", ""),
            "tags": list(info.get("tags") or []),
            "front": vals[0] if vals else "", "back": vals[1] if len(vals) > 1 else ""}


def _notes_info(ids):
    out = []
    for i in range(0, len(ids), INFO_CHUNK):
        out += [n for n in anki.invoke("notesInfo", notes=ids[i:i + INFO_CHUNK]) if n and n.get("noteId")]
    return out


def read_notes(decks):
    ids = set()
    for d in decks:
        ids.update(anki.invoke("findNotes", query=_deck_query(d)))
    return [_note(n) for n in _notes_info(sorted(ids))]


# ---------- cache ----------
def _load():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"notes": {}}


def _save(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def signature(tags, mn, mx, prompt=""):
    """A cached answer only counts for the same tag list, limits and tagging prompt."""
    body = json.dumps([sorted(t.lower() for t in tags), mn, mx, prompt.strip()], ensure_ascii=False)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


# ---------- AI ----------
SYSTEM = """You tag Anki flashcards using a fixed list of tags.

Allowed tags (use these exact spellings and nothing else):
{tag_list}

Rules:
- {count_rule}
- Prefer the most specific tags that fit. A tag with "::" is a child of the part before it: when a
  child fits, use the child instead of its parent. Don't add a broad tag when a narrower allowed tag
  already covers the same thing.
- Order each note's tags from most to least specific. Never repeat a tag. Never invent a tag.
- Answer for every note you are given, exactly once, using its "id".
"""

GUIDE = """
The user's notes on when to use each tag. Follow them, but never break the rules above:
{prompt}
"""
PROMPT_CHARS = 4000

HINT = '{"notes":[{"id":"n1","tags":["..."]}]}'


def _count_rule(mn, mx):
    if mn == mx:
        return f"Give every note exactly {mn} tag{'s' if mn != 1 else ''}."
    if mn == 0:
        return f"Give every note at most {mx} tags. Give no tags if none of them fits."
    return f"Give every note between {mn} and {mx} tags."


def _schema(tags):
    item = {"type": "string"}
    if len(tags) <= ENUM_LIMIT:
        item["enum"] = list(tags)
    return {
        "type": "object",
        "properties": {"notes": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "tags": {"type": "array", "items": item}},
            "required": ["id", "tags"],
            "additionalProperties": False,
        }}},
        "required": ["notes"],
        "additionalProperties": False,
    }


def check_answer(keys, answer, allowed, mn, mx):
    """Split an AI answer into good {key: tags} and bad {key: reason}. allowed maps lowercase -> spelling."""
    good, bad = {}, {}
    items = answer.get("notes") if isinstance(answer, dict) else None
    for it in items if isinstance(items, list) else []:
        key = str(it.get("id", "")) if isinstance(it, dict) else ""
        if key not in keys or key in good or key in bad:
            continue  # unknown or repeated id: ignore, a missing note is caught below
        raw = it.get("tags")
        raw = raw if isinstance(raw, list) else []
        picked, unknown = [], []
        for t in raw:
            canon = allowed.get(re.sub(r"\s+", "_", str(t).strip()).lower())
            if canon is None:
                unknown.append(str(t))
            elif canon not in picked:
                picked.append(canon)
        if unknown:
            bad[key] = "used tags that are not in the list: " + ", ".join(f'"{u}"' for u in unknown)
        elif len(raw) != len(picked):
            bad[key] = "repeated a tag"
        elif not mn <= len(picked) <= mx:
            bad[key] = f"gave {len(picked)} tags, but it must be {mn}" + (f" to {mx}" if mx != mn else "")
        else:
            good[key] = picked
    for key in keys:
        if key not in good and key not in bad:
            bad[key] = "was missing from the answer"
    return good, bad


def tag_batch(provider, settings, notes, tags, mn, mx, job, prompt=""):
    """Returns ({note_id: tags}, {note_id: reason}) for one batch, retrying only the notes that broke the rules."""
    allowed = {t.lower(): t for t in tags}
    system = SYSTEM.format(tag_list="\n".join(f"- {t}" for t in tags), count_rule=_count_rule(mn, mx))
    if prompt.strip():
        system += GUIDE.format(prompt=prompt.strip())
    schema = _schema(tags)
    by_key = {f"n{i + 1}": n for i, n in enumerate(notes)}
    todo, done, bad = list(by_key), {}, {}
    for attempt in range(RETRIES + 1):
        job.check()
        lines = [json.dumps({"id": k, "front": by_key[k]["front"], "back": by_key[k]["back"]}, ensure_ascii=False) for k in todo]
        user = "NOTES (one JSON object per line):\n" + "\n".join(lines) + "\n"
        if attempt:
            user += "\nYour previous answer broke the rules for these notes, fix them:\n" + \
                "\n".join(f"- {k} {bad[k]}" for k in todo) + "\n"
        try:
            answer = llm.ask(provider, settings, system, user, schema, (), job, "anki_tags", HINT)
        except ValueError:  # broken JSON: retried like any other broken answer
            job.check()
            answer = {}
        except RuntimeError as e:
            job.check()
            if "did not return JSON" not in str(e):
                raise  # setup problem (no key, CLI missing): retrying won't help
            answer = {}
        good, bad_now = check_answer(set(todo), answer, allowed, mn, mx)
        done.update(good)
        bad = {**{k: v for k, v in bad.items() if k not in good}, **bad_now}
        todo = [k for k in todo if k in bad_now]
        if not todo:
            break
    return ({by_key[k]["id"]: v for k, v in done.items()},
            {by_key[k]["id"]: f"The AI {bad[k]}." for k in todo})


# ---------- preview ----------
def _lower(tags):
    return {t.lower() for t in tags}


def plan(note, ai_tags, mode):
    """Final tags for a note. Anki compares tags without case."""
    have = _lower(note["tags"])
    if mode == "add":
        after = note["tags"] + [t for t in ai_tags if t.lower() not in have]
    else:
        after = list(ai_tags)
    added = [t for t in after if t.lower() not in have]
    removed = [t for t in note["tags"] if t.lower() not in _lower(after)]
    return after, added, removed


def _row(note, status, **kw):
    return {"id": note["id"], "front": note["front"], "back": note["back"], "before": note["tags"],
            "status": status, **kw}


# ---------- background job ----------
def _set_job(**kw):
    with _lock:
        JOB.update(kw)


def _run(provider, settings, decks, mode, tags, mn, mx, prompt, job):
    try:
        _set_job(phase="read", done=0, total=0)
        notes = read_notes(decks)
        if not notes:
            raise TagError("These decks have no notes.")
        skipped = [n for n in notes if mode == "skip" and n["tags"]]
        targets = [n for n in notes if not (mode == "skip" and n["tags"])]
        sig = signature(tags, mn, mx, prompt)
        allowed = _lower(tags)
        with _lock:
            cached = _load().get("notes", {})
        ai, from_cache = {}, 0
        for n in targets:
            c = cached.get(str(n["id"]))
            if c and c.get("ver") == n["ver"] and c.get("sig") == sig and _lower(c.get("tags", [])) <= allowed:
                ai[n["id"]] = c["tags"]
                from_cache += 1
        todo = [n for n in targets if n["id"] not in ai]
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        failed, error = {}, None
        _set_job(phase="ai", done=0, total=len(todo))
        workers = 3 if provider == "openai" else 2
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(tag_batch, provider, settings, b, tags, mn, mx, job, prompt): b for b in batches}
            for f in as_completed(futures):
                b = futures[f]
                try:
                    good, bad = f.result()
                except llm.Cancelled:
                    continue
                except Exception as e:
                    if not job.cancelled:  # the first real error stops the other batches
                        error = str(e)
                        job.cancel()
                    continue
                ai.update(good)
                failed.update(bad)
                with _lock:  # saved after every batch, so a cancel or error never wastes AI calls
                    cache = _load()
                    store = cache.setdefault("notes", {})
                    for n in b:
                        if n["id"] in good:
                            store[str(n["id"])] = {"ver": n["ver"], "sig": sig, "tags": good[n["id"]]}
                    _save(cache)
                    JOB["done"] += len(b)
        rows, same = [], 0
        for n in targets:
            if n["id"] in failed:
                rows.append(_row(n, "failed", reason=failed[n["id"]]))
            elif n["id"] in ai:
                after, added, removed = plan(n, ai[n["id"]], mode)
                if added or removed:
                    rows.append(_row(n, "change", after=after, added=added, removed=removed, ai=ai[n["id"]]))
                else:
                    same += 1
        summary = {"notes": len(notes), "skipped": len(skipped), "cached": from_cache, "sent": len(todo),
                   "change": sum(r["status"] == "change" for r in rows), "same": same, "failed": len(failed),
                   "not_done": len(targets) - len(ai) - len(failed)}
        if error:
            result = {"kind": "bad", "message": error}
        elif job.cancelled:
            result = {"kind": "warn", "message": "Stopped. Tags found so far are saved, so the next run continues from here."}
        else:
            result = {"kind": "warn" if failed else "good", "message": ""}
        _set_job(rows=rows, summary=summary, mode=mode, result=result)
    except (TagError, anki.AnkiError) as e:
        _set_job(rows=[], summary=None, result={"kind": "bad", "message": str(e)})
    except Exception as e:
        _set_job(rows=[], summary=None, result={"kind": "bad", "message": f"Something went wrong: {e}"})
    finally:
        _current["job"] = None
        _set_job(running=False, phase="")


def start(provider, settings, decks, mode, tags, mn, mx, prompt=""):
    if mode not in MODES:
        raise TagError("Choose what to do with existing tags.")
    decks = [str(d) for d in decks or [] if str(d).strip()]
    if not decks:
        raise TagError("Choose at least one deck.")
    tags = clean_tags(tags)
    mn, mx = check_options(tags, mn, mx)
    prompt = str(prompt or "")
    if len(prompt) > PROMPT_CHARS:
        raise TagError(f"The tagging prompt is too long. Keep it under {PROMPT_CHARS} characters.")
    job = llm.Job()
    with _lock:
        if JOB["running"]:
            return False
        JOB.update(running=True, phase="start", done=0, total=0, result=None, rows=[], summary=None, mode=mode)
        _current["job"] = job
    threading.Thread(target=_run, args=(provider, settings, decks, mode, tags, mn, mx, prompt, job), daemon=True).start()
    return True


def cancel():
    job = _current["job"]
    if job:
        job.cancel()
    return bool(job)


def view():
    with _lock:
        return json.loads(json.dumps(JOB))


# ---------- apply ----------
def _multi(actions):
    """Run (action, params) pairs in chunks. Returns {index: error} for the ones that failed."""
    errors = {}
    for i in range(0, len(actions), APPLY_CHUNK):
        part = actions[i:i + APPLY_CHUNK]
        res = anki.invoke("multi", actions=[{"action": a, "version": 6, "params": p} for a, p in part])
        for j, r in enumerate(res or []):
            if isinstance(r, dict) and r.get("error"):
                errors[i + j] = str(r["error"])
    return errors


def apply(ids):
    """Write the previewed tags of the chosen notes into Anki."""
    want = {int(i) for i in ids}
    with _lock:
        if JOB["running"]:
            raise TagError("Wait until tagging has finished.")
        mode = JOB["mode"]
        rows = [r for r in JOB["rows"] if r["status"] == "change" and r["id"] in want]
    if not rows:
        raise TagError("Nothing to apply.")
    actions = []
    for r in rows:
        if mode == "add":  # addTags merges with whatever the note has now, so edits made meanwhile stay
            actions.append(("addTags", {"notes": [r["id"]], "tags": " ".join(r["added"])}))
        else:
            actions.append(("updateNoteTags", {"note": r["id"], "tags": r["after"]}))
    errors = _multi(actions)
    failed_ids = {rows[i]["id"] for i in errors}
    # Changing tags changes the note's modified time. Store the new one, or the next run would
    # think every tagged note was edited and ask the AI again.
    try:
        fresh = {n["noteId"]: _version(n) for n in _notes_info([r["id"] for r in rows])}
    except anki.AnkiError:
        fresh = {}
    with _lock:
        cache = _load()
        store = cache.setdefault("notes", {})
        for r in rows:
            c = store.get(str(r["id"]))
            if c and r["id"] in fresh:
                c["ver"] = fresh[r["id"]]
        _save(cache)
        for r in JOB["rows"]:
            if r["id"] in want and r["status"] == "change" and r["id"] not in failed_ids:
                r["status"] = "applied"
    return {"applied": len(rows) - len(errors),
            "errors": [f"{(rows[i]['front'] or str(rows[i]['id']))[:40]}: {e}" for i, e in errors.items()]}
