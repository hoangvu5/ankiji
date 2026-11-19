"""Tagging for Mass update: tags from the user's own list only.

autotag_cache.json remembers the AI's tags per note, keyed by note ID + the note's modified
time, so running again only sends new or edited notes to the AI.
"""
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import llm
from .paths import data

CACHE_PATH = data("autotag_cache.json")
BATCH = 30           # notes per AI call
RETRIES = 2          # extra tries for notes whose answer broke the rules
ENUM_LIMIT = 500     # bigger lists go without a schema enum (OpenAI caps enum size); answers are still checked
MODES = ("overwrite", "add", "skip")

_lock = threading.Lock()  # guards the cache file


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


def find_tags(provider, settings, targets, tags, mn, mx, prompt, job, progress):
    """AI tags for the target notes, from the cache when the note and the tag options are unchanged.
    Returns (tags by note id, failure reason by note id, number from cache, error or None)."""
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
    progress(0, len(todo))
    done = 0
    with ThreadPoolExecutor(3 if provider == "openai" else 2) as pool:
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
            done += len(b)
            progress(done, len(todo))
    return ai, failed, from_cache, error


def update_versions(old_to_new):
    """Applying changes a note's modified time. Move cached tags along, or the next run would
    think the note was edited and ask the AI again. old_to_new: {note id: (old ver, new ver)}."""
    with _lock:
        cache = _load()
        store = cache.setdefault("notes", {})
        for nid, (old, new) in old_to_new.items():
            c = store.get(str(nid))
            if c and c.get("ver") == old and new:
                c["ver"] = new
        _save(cache)
