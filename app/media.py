"""Find an example sentence (image + audio + Japanese and English subtitles) on Nadeshiko
for a card, preferring the anime ticked in the Nadeshiko tab."""
import html
import re
import time

import requests

SEARCH_URL = "https://api.nadeshiko.co/v1/search"
CDN = "https://cdn.nadeshiko.co/"
CATEGORIES = ["ANIME", "JDRAMA", "YOUTUBE", "AUDIOBOOK"]
TAKE = 10
ENOUGH = 3          # stop searching wider once this many sentences were found
LONG = 60           # sentences longer than this go to the back of the list
FILE_RE = re.compile(r"nadeshiko_[A-Za-z0-9_-]{12}\.(?:webp|mp3)")


class MediaError(Exception):
    pass


def search_term(value):
    """The word to look up, from a card field: no HTML, no furigana or readings in brackets,
    and only the first item if the field lists several."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|（[^）]*）|【[^】]*】|「|」", "", s)
    for part in re.split(r"[\s,、，/;；・|~〜]+", s.strip()):
        if part:
            return part[:100]
    return ""


def _post(key, body):
    for attempt in range(4):
        try:
            r = requests.post(SEARCH_URL, headers={"Authorization": "Bearer " + key}, json=body, timeout=30)
        except requests.RequestException as e:
            raise MediaError(f"Couldn't reach Nadeshiko: {e}")
        if r.status_code == 429:
            if "quota" in r.text.lower() or attempt == 3:
                raise MediaError("Nadeshiko's request limit was reached (150 a minute, 5,000 a month). Try again later.")
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (401, 403):
            raise MediaError("Nadeshiko rejected the API key. Check it in the Nadeshiko tab.")
        if r.status_code >= 500:
            raise MediaError(f"Nadeshiko had an error ({r.status_code}). Try again in a minute.")
        if not r.ok:
            return {"segments": []}  # it didn't accept this query: same as no result
        return r.json()
    raise MediaError("Nadeshiko is busy. Try again in a minute.")


def _candidate(seg, names, mine):
    sid = seg["publicId"]
    m = names.get(seg["mediaPublicId"]) or {}
    ja = seg["textJa"]["content"]
    # keep only the search highlight (as bold); everything else is escaped text
    parts = [html.escape(html.unescape(re.sub(r"<[^>]+>", "", p)))
             for p in re.split(r"</?(?:em|mark)>", seg["textJa"].get("highlight") or ja)]
    ja_html = "".join(p if i % 2 == 0 else f"<b>{p}</b>" for i, p in enumerate(parts) if p)
    en = (seg.get("textEn") or {}).get("content") or ""
    ep = seg.get("episode")
    return {
        "id": sid,
        "ja": ja,
        "ja_html": ja_html,
        "en": en,
        "en_machine": bool((seg.get("textEn") or {}).get("isMachineTranslated")),
        "image": seg["urls"]["imageUrl"],
        "audio": seg["urls"]["audioUrl"],
        "image_file": f"nadeshiko_{sid}.webp",
        "audio_file": f"nadeshiko_{sid}.mp3",
        "source": (m.get("nameEn") or m.get("nameRomaji") or "") + (f", episode {ep}" if ep else ""),
        "category": m.get("category") or "",
        "mine": seg["mediaPublicId"] in mine,
    }


def _search(key, term, exact, categories, include=None, prefer=None):
    # languages=[]: match the Japanese line only, not the translations
    filters = {"category": categories, "contentRating": ["SAFE", "SUGGESTIVE"], "languages": []}
    if include:
        filters["media"] = {"include": [{"mediaPublicId": i} for i in include[:100]]}
    body = {"query": {"search": term, "exactMatch": exact}, "take": TAKE, "filters": filters, "include": ["media"]}
    if prefer:
        body["preferMedia"] = prefer[:120]
    res = _post(key, body)
    segs = res.get("segments") or []
    names = (res.get("includes") or {}).get("media") or {}
    # human subtitles and short lines first, otherwise keep Nadeshiko's order
    segs.sort(key=lambda s: (bool((s.get("textEn") or {}).get("isMachineTranslated")),
                             len(s["textJa"]["content"]) > LONG))
    return segs, names


def find(key, term, categories, my_ids, fallback):
    """Return {"candidates": [...], "where": "mine" | "other" | "none"}."""
    if not key:
        raise MediaError("Add your Nadeshiko API key in the Nadeshiko tab first.")
    categories = [c for c in categories if c in CATEGORIES]
    if not categories:
        raise MediaError("Tick at least one kind of media to search.")
    term = (term or "").strip()
    if not term:
        return {"candidates": [], "where": "none"}

    steps = []
    if my_ids and "ANIME" in categories:
        steps.append(("mine", dict(include=my_ids)))
    if fallback or not steps:  # with no anime picked, search everything
        steps.append(("other", dict(prefer=my_ids)))

    mine = set(my_ids)
    # The looser search finds other forms (食べた for 食べる) but also unrelated lines,
    # so every line must contain the word's stem.
    stem = term if len(term) < 2 else term[:-1] if len(term) <= 3 else term[:-2]
    out, seen = [], set()
    for exact in (True, False):  # exact phrase first, then looser
        for _, opts in steps:
            segs, names = _search(key, term, exact, categories, **opts)
            for s in segs:
                if stem not in s["textJa"]["content"]:
                    continue
                if s["publicId"] not in seen:
                    seen.add(s["publicId"])
                    out.append(_candidate(s, names, mine))
            if len(out) >= ENOUGH:
                break
        if out:
            break
    where = "none" if not out else ("mine" if out[0]["mine"] else "other")
    return {"candidates": out, "where": where}


def files_to_store(cards_fields, media_files):
    """Which Nadeshiko files the notes actually use, with their download URLs."""
    used = set()
    for fields in cards_fields:
        for v in fields:
            used.update(FILE_RE.findall(v or ""))
    out = {}
    for name in used:
        url = (media_files or {}).get(name, "")
        if url.startswith(CDN):
            out[name] = url
    return out
