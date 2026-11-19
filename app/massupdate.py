"""Mass update: change many notes already in Anki at once.

Pick decks, narrow them with an Anki search (the AI can write it), then preview one action:
add tags, edit fields with the AI, find and replace, or attach a Nadeshiko clip. Nothing is
written until the user applies, and massupdate_undo.json can put the last apply back.
"""
import hashlib
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import anki, autotag, llm, media, nadeshiko
from .paths import data

UNDO_PATH = data("massupdate_undo.json")
ACTIONS = ("tag", "edit", "replace", "media")
INFO_CHUNK = 500     # note IDs per notesInfo call
APPLY_CHUNK = 100    # actions per AnkiConnect multi call
PLAIN_CHARS = 400    # per field for tagging and the preview; the start of a card is enough
EDIT_BATCH = 10      # notes per AI call when editing (whole fields are sent, so fewer than tagging)
EDIT_CHARS = 4000    # longer fields are not sent for editing: the AI may cut them
EDIT_RETRIES = 2
SAMPLE_NOTES = 300   # notes read to learn the note types and fields of the chosen decks
PROMPT_CHARS = 4000

_lock = threading.Lock()  # guards JOB and the undo file
JOB = {"running": False, "phase": "", "done": 0, "total": 0, "result": None, "action": "", "run": "",
       "options": {}, "rows": [], "summary": ""}
_current = {"job": None}  # llm.Job of the running preview, so it can be cancelled


class MassError(Exception):
    pass


# ---------- reading notes ----------
def _deck_query(name):
    # Anki search treats * and _ as wildcards and " ends the name, so escape them.
    return 'deck:"' + re.sub(r'([\\"*_])', r"\\\1", name) + '"'


def full_query(decks, search):
    decks = [str(d) for d in decks or [] if str(d).strip()]
    if not decks:
        raise MassError("Choose at least one deck.")
    q = "(" + " OR ".join(_deck_query(d) for d in decks) + ")"
    search = (search or "").strip()
    return q + (f" ({search})" if search else "")


def plain(value, limit=PLAIN_CHARS):
    v = re.sub(r"\[sound:[^\]]*\]", " ", value or "")
    v = re.sub(r"<br\s*/?>|</div>|</p>", " ", v, flags=re.I)
    v = html.unescape(re.sub(r"<[^>]+>", "", v))
    v = re.sub(r"\s+", " ", v).strip()
    return v[:limit] + ("..." if len(v) > limit else "")


def version(info):
    """Changes whenever the note is edited. Older AnkiConnect has no "mod", then the content decides."""
    if info.get("mod"):
        return str(info["mod"])
    body = json.dumps([info.get("fields"), info.get("tags")], sort_keys=True, ensure_ascii=False)
    return "h" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def _note(info):
    fields = sorted((info.get("fields") or {}).items(), key=lambda kv: kv[1].get("order", 0))
    vals = {k: v.get("value", "") for k, v in fields}
    names = list(vals)
    return {"id": info["noteId"], "ver": version(info), "model": info.get("modelName", ""),
            "tags": list(info.get("tags") or []), "fields": vals,
            "front": plain(vals[names[0]]) if names else "", "back": plain(vals[names[1]]) if len(names) > 1 else ""}


def notes_info(ids):
    out = []
    for i in range(0, len(ids), INFO_CHUNK):
        out += [n for n in anki.invoke("notesInfo", notes=ids[i:i + INFO_CHUNK]) if n and n.get("noteId")]
    return out


def _find_ids(query):
    try:
        return sorted(anki.invoke("findNotes", query=query))
    except anki.AnkiError as e:
        if "connect" in str(e).lower() or "reach" in str(e).lower():
            raise
        raise MassError(f"Anki didn't accept this search: {e}")


def _field_names(notes):
    out = []
    for n in notes:
        for k in n["fields"]:
            if k not in out:
                out.append(k)
    return out


def find(decks, search):
    """Count and sample of the notes a search matches, plus their field names."""
    ids = _find_ids(full_query(decks, search))
    sample = [_note(n) for n in notes_info(ids[:SAMPLE_NOTES])]
    models = {}
    for n in sample:
        models[n["model"]] = models.get(n["model"], 0) + 1
    return {"count": len(ids), "sample": [n["front"] for n in sample[:5]],
            "fields": _field_names(sample), "models": list(models)}


# ---------- the AI writes the search ----------
SEARCH_SYSTEM = """You turn a description of Anki notes into an Anki search query.

Anki search syntax:
- Words match anywhere in any field: dog cat (both), dog OR cat, -dog (not), (a OR b) c, "exact phrase".
- A field: Front:dog means the WHOLE field is exactly "dog". Use * for anything: Front:*dog* contains dog.
  Front: (nothing after the colon) means the field is empty. Front:_* means it is not empty.
  Field names with spaces go in quotes: "Word Meaning:*x*". Field searches ignore case.
- Regex: "Front:re:^\\d+$" or re:pattern for any field. Without accents: nc:uber.
- Tags: tag:food, tag:grammar::* (with children), tag:none (no tags), -tag:food.
- Note type: note:Basic, "note:Japanese Card Format".
- Cards: is:new, is:learn, is:review, is:due, is:suspended, is:buried, flag:1 (red) to flag:7.
- Properties: prop:ivl>=30 (interval days), prop:due=1 (due tomorrow), prop:lapses>3, prop:reps>10,
  prop:ease<2.5. Added in the last n days: added:7. Edited: edited:7. Answered: rated:7, rated:7:1 (pressed Again).
- Media files are referenced inside fields: images as <img src="name.webp">, audio and video as [sound:name.mp3].
  So "has an mp3" is Media:*.mp3*, "has an image" is Media:*<img*.
- Escape a literal * or _ with a backslash, and a literal " with \\".

The app already limits the search to the decks the user chose, so never add deck:.
Use the note types and field names below; match the field the user means by its meaning.
Answer with the query and a one-line plain explanation of what it finds.

Note types and fields in the chosen decks (with one example value each):
{fields}
"""
SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "explain": {"type": "string"}},
    "required": ["query", "explain"],
    "additionalProperties": False,
}


def _fields_context(decks):
    ids = _find_ids(full_query(decks, ""))
    notes = [_note(n) for n in notes_info(ids[:SAMPLE_NOTES])]
    by_model = {}
    for n in notes:
        m = by_model.setdefault(n["model"], {})
        for k, v in n["fields"].items():
            if not m.get(k) and v.strip():
                m[k] = v
            m.setdefault(k, "")
    lines = []
    for model, fields in by_model.items():
        lines.append(f'Note type "{model}":')
        for k, v in fields.items():
            lines.append(f"  - {k}: " + (json.dumps(v[:150], ensure_ascii=False) if v else "(empty in the sample)"))
    return "\n".join(lines) or "(no notes in these decks)"


def write_search(provider, settings, decks, text):
    text = (text or "").strip()
    if not text:
        raise MassError("Describe the notes you want first.")
    system = SEARCH_SYSTEM.format(fields=_fields_context(decks))
    user = "Which notes: " + text[:PROMPT_CHARS]
    for attempt in range(2):
        ans = llm.ask(provider, settings, system, user, SEARCH_SCHEMA, (), None, "anki_search",
                      '{"query":"...","explain":"..."}')
        q = str(ans.get("query") or "").strip()
        q = re.sub(r'(?i)(^|\s)-?"?deck:("[^"]*"|\S+)"?', " ", q).strip()  # the decks are added by the app
        try:
            count = len(_find_ids(full_query(decks, q)))
            return {"query": q, "explain": str(ans.get("explain") or ""), "count": count}
        except MassError as e:
            if attempt:
                raise
            user += f"\n\nYour query {q!r} was rejected by Anki ({e}). Write a valid one."


# ---------- actions ----------
def _row(n, **kw):
    return {"id": n["id"], "ver": n["ver"], "front": n["front"], "back": n["back"],
            "status": "change", "fields": {}, "files": {}, **kw}


def _changed(a, b):
    return (a or "").strip() != (b or "").strip()


# Find and replace
def replacer(find_text, repl, regex, case):
    if not find_text:
        raise MassError("Type what to find.")
    flags = 0 if case else re.I
    try:
        pat = re.compile(find_text if regex else re.escape(find_text), flags)
    except re.error as e:
        raise MassError(f"The pattern isn't valid: {e}")
    if regex:
        # people often write $1 for a group, as in most editors; Python wants \g<1>
        repl = re.sub(r"\$(\d+)", r"\\g<\1>", repl)
        try:
            pat.sub(repl, "")
        except (re.error, IndexError) as e:
            raise MassError(f"The replacement isn't valid: {e}")
        return lambda v: pat.sub(repl, v)
    return lambda v: pat.sub(lambda _m: repl, v)


def _do_replace(notes, opt):
    fn = replacer(opt.get("find", ""), opt.get("replace", ""), opt.get("regex"), opt.get("case"))
    want = opt.get("fields") or []
    rows, same = [], 0
    for n in notes:
        ch = {}
        for k, v in n["fields"].items():
            if want and k not in want:
                continue
            new = fn(v)
            if new != v:
                ch[k] = [v, new]
        if ch:
            rows.append(_row(n, fields=ch))
        else:
            same += 1
    return rows, {"same": same}


# Edit with AI
EDIT_SYSTEM = """You edit Anki notes in bulk, following the user's instructions.

Fields you may change, and the answer key for each:
{keys}
Other fields are shown for context only.

Rules:
- Change only what the instructions ask for. Keep everything else exactly as it is, character for
  character, including HTML tags, [sound:...] tags, image tags, furigana like 漢字[かんじ] and line breaks.
- If a note needs no change, return its fields unchanged. Never shorten or summarise a field unless asked.
- If a note does not have one of the fields, return "" for it.
- Answer for every note you are given, exactly once, using its "id".

The user's instructions:
{prompt}
"""


def _edit_schema(n):
    props = {"id": {"type": "string"}, **{f"f{i}": {"type": "string"} for i in range(n)}}
    return {
        "type": "object",
        "properties": {"notes": {"type": "array", "items": {
            "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}},
        "required": ["notes"],
        "additionalProperties": False,
    }


def edit_batch(provider, settings, notes, fields, prompt, job):
    """Returns ({note_id: {field: new value}}, {note_id: reason})."""
    keys = {f"f{i}": f for i, f in enumerate(fields)}
    system = EDIT_SYSTEM.format(keys="\n".join(f'- {k} = "{f}"' for k, f in keys.items()), prompt=prompt.strip())
    schema = _edit_schema(len(fields))
    hint = '{"notes":[{"id":"n1",' + ",".join(f'"{k}":"..."' for k in keys) + "}]}"
    by_key = {f"n{i + 1}": n for i, n in enumerate(notes)}
    todo, done = list(by_key), {}
    for attempt in range(EDIT_RETRIES + 1):
        job.check()
        lines = [json.dumps({"id": k, "note_type": by_key[k]["model"], "fields": by_key[k]["fields"]}, ensure_ascii=False)
                 for k in todo]
        user = "NOTES (one JSON object per line):\n" + "\n".join(lines) + "\n"
        if attempt:
            user += "\nThese notes were missing from your previous answer, answer them: " + ", ".join(todo) + "\n"
        try:
            answer = llm.ask(provider, settings, system, user, schema, (), job, "anki_edits", hint)
        except ValueError:
            job.check()
            answer = {}
        except RuntimeError as e:
            job.check()
            if "did not return JSON" not in str(e):
                raise
            answer = {}
        items = answer.get("notes") if isinstance(answer, dict) else None
        for it in items if isinstance(items, list) else []:
            k = str(it.get("id", "")) if isinstance(it, dict) else ""
            if k not in todo or k in done:
                continue
            note = by_key[k]
            done[k] = {f: str(it.get(fk, "")) for fk, f in keys.items() if f in note["fields"] and isinstance(it.get(fk), str)}
        todo = [k for k in todo if k not in done]
        if not todo:
            break
    return ({by_key[k]["id"]: v for k, v in done.items()},
            {by_key[k]["id"]: "The AI didn't answer for this note." for k in todo})


def _do_edit(notes, opt, provider, settings, job):
    fields = [f for f in opt.get("fields") or [] if f]
    prompt = str(opt.get("prompt") or "")
    if not fields:
        raise MassError("Tick at least one field the AI may change.")
    if not prompt.strip():
        raise MassError("Write what the AI should change.")
    if len(prompt) > PROMPT_CHARS:
        raise MassError(f"The instructions are too long. Keep them under {PROMPT_CHARS} characters.")
    rows, targets = [], []
    for n in notes:
        if not any(f in n["fields"] for f in fields):
            continue
        if any(len(v) > EDIT_CHARS for v in n["fields"].values()):
            rows.append(_row(n, status="failed", reason=f"A field is longer than {EDIT_CHARS} characters, so it isn't sent to the AI."))
        else:
            targets.append(n)
    batches = [targets[i:i + EDIT_BATCH] for i in range(0, len(targets), EDIT_BATCH)]
    new, failed, error, done = {}, {}, None, 0
    _set_job(phase="ai", done=0, total=len(targets))
    with ThreadPoolExecutor(3 if provider == "openai" else 2) as pool:
        futures = {pool.submit(edit_batch, provider, settings, b, fields, prompt, job): b for b in batches}
        for f in as_completed(futures):
            try:
                good, bad = f.result()
            except llm.Cancelled:
                continue
            except Exception as e:
                if not job.cancelled:
                    error = str(e)
                    job.cancel()
                continue
            new.update(good)
            failed.update(bad)
            done += len(futures[f])
            _set_job(done=done)
    same = 0
    for n in targets:
        if n["id"] in failed:
            rows.append(_row(n, status="failed", reason=failed[n["id"]]))
        elif n["id"] in new:
            ch = {k: [n["fields"][k], v] for k, v in new[n["id"]].items() if _changed(n["fields"][k], v)}
            if ch:
                rows.append(_row(n, fields=ch))
            else:
                same += 1
    return rows, {"same": same, "sent": len(targets), "not_done": len(targets) - len(new) - len(failed), "error": error}


# Add tags
def _do_tag(notes, opt, provider, settings, job):
    mode = opt.get("mode")
    if mode not in autotag.MODES:
        raise MassError("Choose what to do with cards that already have tags.")
    tags = autotag.clean_tags(opt.get("tags"))
    try:
        mn, mx = autotag.check_options(tags, opt.get("min"), opt.get("max"))
    except autotag.TagError as e:
        raise MassError(str(e))
    prompt = str(opt.get("prompt") or "")
    if len(prompt) > PROMPT_CHARS:
        raise MassError(f"The tagging prompt is too long. Keep it under {PROMPT_CHARS} characters.")
    skipped = [n for n in notes if mode == "skip" and n["tags"]]
    targets = [n for n in notes if not (mode == "skip" and n["tags"])]
    _set_job(phase="ai")
    ai, failed, cached, error = autotag.find_tags(provider, settings, targets, tags, mn, mx, prompt, job,
                                                  lambda d, t: _set_job(done=d, total=t))
    rows, same = [], 0
    for n in targets:
        if n["id"] in failed:
            rows.append(_row(n, status="failed", reason=failed[n["id"]], tags=[n["tags"], n["tags"]]))
        elif n["id"] in ai:
            after, added, removed = autotag.plan(n, ai[n["id"]], mode)
            if added or removed:
                rows.append(_row(n, tags=[n["tags"], after], added=added, removed=removed))
            else:
                same += 1
    return rows, {"same": same, "skipped": len(skipped), "cached": cached, "sent": len(targets) - cached,
                  "not_done": len(targets) - len(ai) - len(failed), "error": error}


# Attach media clip
def _do_media(notes, opt, settings, job):
    key = settings.get("nadeshiko_api_key", "")
    if not key:
        raise MassError("Add your Nadeshiko API key in the Nadeshiko tab first.")
    target, word = opt.get("field") or "", opt.get("word_field") or ""
    if not target:
        raise MassError("Choose the field the clip goes into.")
    replace = bool(opt.get("replace"))
    cats = settings.get("media_categories") or ["ANIME"]
    fallback = bool(settings.get("media_fallback", True))
    mine = nadeshiko.my_ids()
    counts = {"no_field": 0, "had_media": 0, "same": 0}
    todo = []
    for n in notes:
        if target not in n["fields"]:
            counts["no_field"] += 1
        elif n["fields"][target].strip() and not replace:
            counts["had_media"] += 1
        else:
            todo.append(n)
    _set_job(phase="media", done=0, total=len(todo))

    def one(n):
        job.check()
        old = n["fields"][target]
        m = media.ID_RE.search(old)
        c = media.segment(key, m.group(1), mine) if m else None
        how = "same sentence" if c else "new search"
        if not c:
            w = n["fields"].get(word) if word in n["fields"] else next(iter(n["fields"].values()), "")
            term = media.search_term(w)
            found = media.find(key, term, cats, mine, fallback)["candidates"] if term else []
            if not found:
                return n, None, f'No sentence found for "{term}".' if term else "The word field is empty."
            c = found[0]
        return n, c, how

    rows, stop, done = [], None, 0
    with ThreadPoolExecutor(3) as pool:
        futures = {pool.submit(one, n): n for n in todo}
        for f in as_completed(futures):
            try:
                n, c, how = f.result()
            except llm.Cancelled:
                continue
            except media.MediaError as e:
                if not job.cancelled:  # bad key or limit reached: the rest would fail the same way
                    stop = str(e)
                    job.cancel()
                continue
            except Exception as e:
                n, c, how = futures[f], None, f"Something went wrong: {e}"
            done += 1
            _set_job(done=done)
            if not c:
                rows.append(_row(n, status="failed", reason=how))
                continue
            new = media.field_html(c)
            if not _changed(n["fields"][target], new):
                counts["same"] += 1
                continue
            rows.append(_row(n, fields={target: [n["fields"][target], new]}, files={c["video_file"]: c["video"]},
                             note=("Same sentence as before, as a video clip" if how == "same sentence" else
                                   ("Your anime: " if c["mine"] else "Other title: ") + c["source"])))
    order = {n["id"]: i for i, n in enumerate(todo)}
    rows.sort(key=lambda r: order[r["id"]])
    return rows, {**counts, "not_done": len(todo) - done, "error": stop}


# ---------- background job ----------
def _set_job(**kw):
    with _lock:
        JOB.update(kw)


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _summary(action, total, rows, extra):
    change = sum(r["status"] == "change" for r in rows)
    failed = sum(r["status"] == "failed" for r in rows)
    parts = [f"Found {_plural(total, 'note')}."]
    if extra.get("skipped"):
        parts.append(f"Skipped {extra['skipped']} that already had tags.")
    if extra.get("no_field"):
        parts.append(f"{extra['no_field']} have no such field.")
    if extra.get("had_media"):
        parts.append(f"{extra['had_media']} already have media (tick Replace to change them).")
    if action in ("tag", "edit") and "sent" in extra:
        parts.append(f"{extra['sent']} sent to the AI" + (f", {extra['cached']} from earlier runs." if extra.get("cached") else "."))
    parts.append(f"{change} to change, {extra.get('same', 0)} already fine.")
    if failed:
        parts.append(f"{_plural(failed, 'note')} could not be done (see the table).")
    if extra.get("not_done"):
        parts.append(f"{extra['not_done']} not done yet.")
    return " ".join(parts)


def _run(action, provider, settings, decks, search, opt, job):
    try:
        _set_job(phase="read", done=0, total=0)
        notes = [_note(n) for n in notes_info(_find_ids(full_query(decks, search)))]
        if not notes:
            raise MassError("No notes match. Change the decks or the search.")
        if action == "tag":
            rows, extra = _do_tag(notes, opt, provider, settings, job)
        elif action == "edit":
            rows, extra = _do_edit(notes, opt, provider, settings, job)
        elif action == "replace":
            rows, extra = _do_replace(notes, opt)
        else:
            rows, extra = _do_media(notes, opt, settings, job)
        error = extra.get("error")
        if error:
            result = {"kind": "bad", "message": error}
        elif job.cancelled:
            result = {"kind": "warn", "message": "Stopped." + (" Tags found so far are saved, so the next run continues from here." if action == "tag" else "")}
        else:
            result = {"kind": "warn" if any(r["status"] == "failed" for r in rows) else "good", "message": ""}
        _set_job(rows=rows, summary=_summary(action, len(notes), rows, extra), result=result)
    except (MassError, anki.AnkiError, media.MediaError) as e:
        _set_job(rows=[], summary="", result={"kind": "bad", "message": str(e)})
    except Exception as e:
        _set_job(rows=[], summary="", result={"kind": "bad", "message": f"Something went wrong: {e}"})
    finally:
        _current["job"] = None
        _set_job(running=False, phase="")


def start(action, provider, settings, decks, search, opt):
    if action not in ACTIONS:
        raise MassError("Choose what to do.")
    full_query(decks, search)  # checks the decks before starting
    if action == "replace":
        replacer(opt.get("find", ""), opt.get("replace", ""), opt.get("regex"), opt.get("case"))
    job = llm.Job()
    with _lock:
        if JOB["running"]:
            return False
        JOB.update(running=True, phase="start", done=0, total=0, result=None, rows=[], summary="",
                   action=action, options=opt, run=str(time.time()))
        _current["job"] = job
    threading.Thread(target=_run, args=(action, provider, settings, decks, search, opt, job), daemon=True).start()
    return True


def cancel():
    job = _current["job"]
    if job:
        job.cancel()
    return bool(job)


def _undo_info():
    u = _load_undo()
    return {"count": len(u["notes"]), "action": u.get("action", ""), "time": u.get("time", 0)} if u.get("notes") else None


def view():
    with _lock:
        out = json.loads(json.dumps(JOB))
        out["undo"] = _undo_info()
    return out


# ---------- apply and undo ----------
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


def _load_undo():
    try:
        with open(UNDO_PATH, encoding="utf-8") as f:
            u = json.load(f)
            return u if isinstance(u.get("notes"), list) else {"notes": []}
    except (OSError, ValueError, AttributeError):
        return {"notes": []}


def _save_undo(u):
    if not u.get("notes"):
        try:
            os.remove(UNDO_PATH)
        except OSError:
            pass
        return
    tmp = UNDO_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False)
    os.replace(tmp, UNDO_PATH)


def _label(r):
    return (r.get("front") or str(r["id"]))[:40]


def apply(ids):
    """Write the previewed changes of the chosen notes into Anki."""
    want = {int(i) for i in ids}
    with _lock:
        if JOB["running"]:
            raise MassError("Wait until the preview has finished.")
        action, run, opt = JOB["action"], JOB["run"], JOB["options"]
        rows = [r for r in JOB["rows"] if r["status"] == "change" and r["id"] in want]
    if not rows:
        raise MassError("Nothing to apply.")
    fresh = {n["noteId"]: n for n in notes_info([r["id"] for r in rows])}
    errors, ok = [], []
    for r in rows:
        n = fresh.get(r["id"])
        if not n:
            errors.append(f"{_label(r)}: the note is gone from Anki.")
        elif version(n) != r["ver"]:
            errors.append(f"{_label(r)}: edited in Anki after the preview, so it was left alone. Preview again to include it.")
        else:
            ok.append(r)
    files = {}
    for r in ok:
        files.update(r.get("files") or {})
    for fname, url in media.files_to_store([list(files)], files).items():
        try:  # Anki downloads the clip into its media folder
            anki.invoke("storeMediaFile", filename=fname, url=url)
        except anki.AnkiError as e:
            errors.append(f"{fname}: {e}")
    actions, owner = [], []
    for r in ok:
        if r["fields"]:
            actions.append(("updateNoteFields", {"note": {"id": r["id"], "fields": {k: v[1] for k, v in r["fields"].items()}}}))
            owner.append(r)
        if "tags" in r:
            if opt.get("mode") == "add":  # addTags merges with whatever the note has now
                actions.append(("addTags", {"notes": [r["id"]], "tags": " ".join(r["added"])}))
            else:
                actions.append(("updateNoteTags", {"note": r["id"], "tags": r["tags"][1]}))
            owner.append(r)
    bad = _multi(actions) if actions else {}
    failed_ids = set()
    for i, e in bad.items():
        failed_ids.add(owner[i]["id"])
        errors.append(f"{_label(owner[i])}: {e}")
    done = [r for r in ok if r["id"] not in failed_ids]
    try:
        after = {n["noteId"]: version(n) for n in notes_info([r["id"] for r in done])}
    except anki.AnkiError:
        after = {}
    if action in ("tag", "media"):  # a clip or new tags don't change what the note is about
        autotag.update_versions({r["id"]: (r["ver"], after.get(r["id"])) for r in done})
    with _lock:
        u = _load_undo()
        if u.get("run") != run:
            u = {"run": run, "action": action, "notes": []}
        u["time"] = time.time()
        have = {e["id"] for e in u["notes"]}
        for r in done:
            if r["id"] in have:
                continue
            n = fresh[r["id"]]
            u["notes"].append({"id": r["id"], "ver": after.get(r["id"], ""),
                               "fields": {k: n["fields"][k]["value"] for k in r["fields"] if k in n["fields"]},
                               "tags": list(n.get("tags") or []) if "tags" in r else None})
        _save_undo(u)
        for r in JOB["rows"]:
            if r["id"] in {d["id"] for d in done} and r["status"] == "change":
                r["status"] = "applied"
                r["ver"] = after.get(r["id"], r["ver"])
    return {"applied": len(done), "errors": errors}


def undo():
    with _lock:
        if JOB["running"]:
            raise MassError("Wait until the preview has finished.")
        u = _load_undo()
    if not u.get("notes"):
        raise MassError("There is nothing to undo.")
    fresh = {n["noteId"]: n for n in notes_info([e["id"] for e in u["notes"]])}
    actions, owner, errors, todo = [], [], [], []
    for e in u["notes"]:
        n = fresh.get(e["id"])
        if not n:
            errors.append(f"Note {e['id']}: gone from Anki.")
        elif e.get("ver") and version(n) != e["ver"]:
            errors.append(f"{_note(n)['front'][:40] or e['id']}: edited in Anki after the apply, so it was left as it is now.")
        else:
            todo.append(e)
    for e in todo:
        if e.get("fields"):
            actions.append(("updateNoteFields", {"note": {"id": e["id"], "fields": e["fields"]}}))
            owner.append(e)
        if e.get("tags") is not None:
            actions.append(("updateNoteTags", {"note": e["id"], "tags": e["tags"]}))
            owner.append(e)
    bad = _multi(actions) if actions else {}
    failed = {owner[i]["id"] for i in bad}
    errors += [f"Note {owner[i]['id']}: {err}" for i, err in bad.items()]
    restored = {e["id"] for e in todo if e["id"] not in failed}
    try:
        now = {n["noteId"]: version(n) for n in notes_info(sorted(restored))}
    except anki.AnkiError:
        now = {}
    with _lock:
        u["notes"] = [e for e in u["notes"] if e["id"] in failed]  # only a failed restore can be tried again
        _save_undo(u)
        for r in JOB["rows"]:
            if r["id"] in restored and r["status"] == "applied":
                r["status"] = "change"
                r["ver"] = now.get(r["id"], r["ver"])
    return {"restored": len(restored), "errors": errors}
