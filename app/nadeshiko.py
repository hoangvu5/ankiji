"""MyAnimeList -> Nadeshiko ID lookup with a local cache.

nadeshiko_cache.json remembers every anime already searched, so Nadeshiko
(5,000 requests a month) is only asked about anime that are new on the MAL list.
"""
import json
import os
import re
import threading
import time
import unicodedata
from datetime import datetime

import requests

from .paths import data

CACHE_PATH = data("nadeshiko_cache.json")

MAL_LIST_URL = "https://myanimelist.net/animelist/{user}/load.json"
NADESHIKO_URL = "https://api.nadeshiko.co/v1/search/media"
MAL_STATUSES = {1: "watching", 2: "completed"}
MAX_IDS = 100  # the Shortcut only uses this many
SEARCH_DELAY = 0.25
MAX_FAILS_IN_A_ROW = 3

_lock = threading.Lock()  # guards the cache file and JOB
JOB = {"running": False, "done": 0, "total": 0, "current": "", "phase": "", "result": None}


class StopSearching(Exception):
    """Nadeshiko refused (bad key or limit reached), so further searches are pointless."""


class LookupFailed(Exception):
    """Temporary problem. The anime stays unsearched and is tried again next time."""


class MalError(Exception):
    pass


# ---------- cache ----------
def _load():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"anime": {}}


def _save(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_PATH)


# ---------- MyAnimeList ----------
def parse_user(text):
    text = (text or "").strip()
    m = re.search(r"myanimelist\.net/(?:profile|animelist)/([\w-]+)", text, re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]+", text):
        return text
    raise MalError("That doesn't look like a MyAnimeList profile link. Example: https://myanimelist.net/profile/YourName")


def fetch_mal(user):
    """Return {mal_id: entry} for Watching + Completed. Raises on any failure, so a
    bad response can never look like "you removed everything"."""
    found = {}
    for status, label in MAL_STATUSES.items():
        offset = 0
        while True:
            try:
                r = requests.get(MAL_LIST_URL.format(user=user), params={"status": status, "offset": offset}, timeout=30)
            except requests.RequestException as e:
                raise MalError(f"Couldn't reach MyAnimeList: {e}")
            if r.status_code == 400:
                raise MalError(f'MyAnimeList wouldn\'t show the list for "{user}". Check the name, and that your anime list is public.')
            if not r.ok:
                raise MalError(f"MyAnimeList returned an error ({r.status_code}). Try again in a minute.")
            page = r.json()
            if not page:
                break
            for a in page:
                found[str(a["anime_id"])] = {
                    "id": a["anime_id"],
                    "title": str(a.get("anime_title") or ""),  # numeric titles can arrive as numbers
                    "title_en": str(a.get("anime_title_eng") or ""),
                    "status": label,
                }
            offset += len(page)
            time.sleep(0.5)
    return found


def merge(cache, mal):
    """Apply the fresh MAL list to the cache. Returns (added, removed) titles."""
    anime = cache.setdefault("anime", {})
    removed = [anime.pop(k)["title"] for k in list(anime) if k not in mal]
    added = []
    for k, m in mal.items():
        if k in anime:
            anime[k].update(m)  # title or watching/completed may have changed
        else:
            anime[k] = {**m, "searched": False, "match": None, "sure": False, "use": False}
            added.append(m["title"])
    return added, removed


# ---------- Nadeshiko ----------
def norm(s):
    return "".join(c for c in unicodedata.normalize("NFKC", s or "").lower() if c.isalnum())


def lookup(key, query):
    for attempt in range(3):
        try:
            r = requests.post(NADESHIKO_URL, headers={"Authorization": "Bearer " + key},
                              json={"query": query, "take": 10}, timeout=30)
        except requests.RequestException as e:
            raise LookupFailed(str(e))
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (401, 403):
            raise StopSearching("Nadeshiko rejected the API key. Check it and try again.")
        if r.status_code >= 500:
            raise LookupFailed(f"Nadeshiko error {r.status_code}")
        if not r.ok:
            return []  # it didn't accept this title as a query: same as no result
        return r.json().get("media") or []
    raise StopSearching("Nadeshiko's request limit was reached (150 a minute, 5,000 a month). Anime not searched yet will be tried next time.")


def pick(results, title, title_en):
    want = {n for n in (norm(title), norm(title_en)) if n}
    for m in results:
        if want & {norm(m.get("nameRomaji")), norm(m.get("nameEn"))}:
            return m, True
    return (results[0], False) if results else (None, False)


def find_match(key, title, title_en):
    match, sure = pick(lookup(key, title), title, title_en)
    if not sure and title_en:
        time.sleep(SEARCH_DELAY)
        m2, sure2 = pick(lookup(key, title_en), title, title_en)
        if m2 and (sure2 or not match):
            match, sure = m2, sure2
    return match, sure


# ---------- background job ----------
def _set_job(**kw):
    with _lock:
        JOB.update(kw)


def _search_pending(key):
    """Search only the anime not searched yet. Returns (searched, failed, stop_message)."""
    with _lock:
        pending = [a for a in _load()["anime"].values() if not a["searched"]]
    pending.sort(key=lambda a: a["title"].lower())
    if pending and not key:
        return 0, len(pending), "Add your Nadeshiko API key to search the anime that are new."
    searched = failed = in_a_row = 0
    for i, a in enumerate(pending):
        _set_job(phase="search", done=i, total=len(pending), current=a["title_en"] or a["title"])
        try:
            match, sure = find_match(key, a["title"], a["title_en"])
        except StopSearching as e:
            return searched, failed + len(pending) - i, str(e)
        except LookupFailed:
            failed += 1
            in_a_row += 1
            if in_a_row >= MAX_FAILS_IN_A_ROW:
                return searched, failed + len(pending) - i - 1, "Couldn't reach Nadeshiko. Anime not searched yet will be tried next time."
            continue
        in_a_row = 0
        searched += 1
        keep = None
        if match:
            keep = {f: match.get(f) or "" for f in ("publicId", "nameRomaji", "nameEn", "nameJa")}
        with _lock:  # saved after every anime, so stopping never wastes a search
            cache = _load()
            entry = cache["anime"].get(str(a["id"]))
            if entry:
                entry.update(searched=True, match=keep, sure=sure, use=sure)
                _save(cache)
        time.sleep(SEARCH_DELAY)
    return searched, failed, None


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _sync_message(mal_total, added, removed, searched, failed):
    parts = []
    if mal_total is not None:
        parts.append(f"MyAnimeList has {mal_total} anime in Watching and Completed.")
        parts.append(f"{len(added)} new, {len(removed)} removed.")
        if removed:
            parts.append("Removed: " + ", ".join(removed) + ".")
    if searched:
        parts.append(f"Searched Nadeshiko for {_plural(searched, 'title')}.")
    elif not failed:
        parts.append("Nadeshiko wasn't searched, nothing new to look up.")
    if failed:
        parts.append(f"{failed} could not be searched and will be tried next time.")
    return " ".join(parts)


def _run(key, profile):
    """profile=None skips MyAnimeList and only searches what's pending."""
    try:
        added, removed, mal_total = [], [], None
        if profile is not None:
            _set_job(phase="mal", done=0, total=0, current="")
            user = parse_user(profile)
            mal = fetch_mal(user)
            with _lock:
                cache = _load()
                if not mal and cache.get("anime"):
                    raise MalError("MyAnimeList returned an empty list, so nothing was changed. Try again.")
                added, removed = merge(cache, mal)
                cache["mal_user"] = user
                cache["last_sync"] = datetime.now().isoformat(timespec="seconds")
                _save(cache)
            mal_total = len(mal)
        searched, failed, stop = _search_pending(key)
        message = _sync_message(mal_total, added, removed, searched, failed)
        if stop:
            result = {"kind": "warn", "message": f"{stop} {message}"}
        else:
            result = {"kind": "warn" if failed else "good", "message": message}
    except MalError as e:
        result = {"kind": "bad", "message": str(e)}
    except Exception as e:
        result = {"kind": "bad", "message": f"Something went wrong: {e}"}
    _set_job(running=False, phase="", current="", result=result)


def start_sync(key, profile):
    with _lock:
        if JOB["running"]:
            return False
        JOB.update(running=True, phase="start", done=0, total=0, current="", result=None)
    threading.Thread(target=_run, args=(key, profile), daemon=True).start()
    return True


def search_again(key, anime_id):
    with _lock:
        if JOB["running"]:
            return False
        cache = _load()
        entry = cache["anime"].get(str(anime_id))
        if not entry:
            return False
        entry.update(searched=False, match=None, sure=False, use=False)
        _save(cache)
    return start_sync(key, None)


def set_use(anime_id, use):
    with _lock:
        cache = _load()
        entry = cache["anime"].get(str(anime_id))
        if entry and entry["match"]:
            entry["use"] = bool(use)
            _save(cache)


def my_ids():
    """Nadeshiko IDs of the anime ticked "Use", in list order, no repeats."""
    with _lock:
        anime = list(_load().get("anime", {}).values())
    ids = []
    for a in anime:
        if a.get("use") and a.get("match") and a["match"].get("publicId") not in ids:
            ids.append(a["match"]["publicId"])
    return ids


# ---------- what the page shows ----------
def _state_of(a):
    if not a["searched"]:
        return "pending"
    if not a["match"]:
        return "missing"
    return "matched" if a["sure"] else "check"


def view():
    with _lock:
        cache = _load()
        job = dict(JOB)
    order = {"pending": 0, "check": 1, "matched": 2, "missing": 3}
    rows = [{**a, "state": _state_of(a)} for a in cache.get("anime", {}).values()]
    rows.sort(key=lambda r: (order[r["state"]], (r["title_en"] or r["title"]).lower()))
    ids = []
    for r in rows:
        if r["use"] and r["match"]["publicId"] not in ids:
            ids.append(r["match"]["publicId"])
    counts = {s: sum(r["state"] == s for r in rows) for s in order}
    return {
        "job": job,
        "rows": rows,
        "counts": counts,
        "selected": len(ids),
        "json": json.dumps([{"mediaPublicId": i} for i in ids[:MAX_IDS]], ensure_ascii=False),
        "over_limit": len(ids) > MAX_IDS,
        "mal_user": cache.get("mal_user", ""),
        "last_sync": cache.get("last_sync", ""),
    }
