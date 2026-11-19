"""Find an example sentence (video clip + Japanese and English subtitles) on Nadeshiko
for a card, preferring the anime ticked in the Nadeshiko tab."""
import html
import re
import time

import requests

SEARCH_URL = "https://api.nadeshiko.co/v1/search"
SEGMENT_URL = "https://api.nadeshiko.co/v1/media/segments/"
CDN = "https://cdn.nadeshiko.co/"
CATEGORIES = ["ANIME", "JDRAMA", "YOUTUBE", "AUDIOBOOK"]
TAKE = 10
ENOUGH = 3          # stop searching wider once this many sentences were found
LONG = 60           # sentences longer than this go to the back of the list
FILE_RE = re.compile(r"nadeshiko_[A-Za-z0-9_-]{12}(?:_[0-9a-f]{3})?\.(?:mp4|webp|mp3)")
OLD_ID_RE = re.compile(r"nadeshiko_([A-Za-z0-9_-]{12})\.(?:mp4|webp|mp3)")
CLIP_RE = re.compile(r"nadeshiko_([a-z0-9_-]{12})_([0-9a-f]{3})\.mp4")


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


def _post(key, body, url=SEARCH_URL):
    for attempt in range(4):
        try:
            if body is None:
                r = requests.get(url, headers={"Authorization": "Bearer " + key}, timeout=30)
            else:
                r = requests.post(url, headers={"Authorization": "Bearer " + key}, json=body, timeout=30)
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
            return {"segments": []} if body is not None else {}  # not accepted or not found: no result
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
        "video": seg["urls"]["videoUrl"],
        "video_file": clip_name(sid),
        "source": (m.get("nameEn") or m.get("nameRomaji") or "") + (f", episode {ep}" if ep else ""),
        "category": m.get("category") or "",
        "mine": seg["mediaPublicId"] in mine,
    }


def clip_name(sid):
    """File name for a sentence's clip. Nadeshiko IDs mix upper and lower case, but Windows and
    Anki's media folder don't keep case apart: Anki saved "eY2" as "ey2", so the cards pointed at a
    name the iPhone couldn't find. The name is lowercase, plus which letters were capitals."""
    mask = sum(1 << i for i, ch in enumerate(sid) if ch.isupper())
    return f"nadeshiko_{sid.lower()}_{mask:03x}.mp4"


def sentence_id(value):
    """The Nadeshiko ID a field's media came from, or None."""
    m = CLIP_RE.search(value or "")
    if m:
        mask = int(m.group(2), 16)
        return "".join(ch.upper() if mask >> i & 1 else ch for i, ch in enumerate(m.group(1)))
    m = OLD_ID_RE.search(value or "")
    return m.group(1) if m else None


def store(invoke, files):
    """Have Anki download each clip. Returns ({asked name: name Anki saved it as}, errors).
    Anki can save a file under another name, and the cards must use the name it chose."""
    saved, errors = {}, []
    for fname, url in files.items():
        if not url.startswith(CDN):
            continue
        try:
            got = invoke("storeMediaFile", filename=fname, url=url)
            saved[fname] = got if isinstance(got, str) and got else fname
        except Exception as e:
            errors.append(f"{fname}: {e}")
    return saved, errors


def rename(value, saved):
    for old, new in saved.items():
        if old != new:
            value = value.replace(old, new)
    return value


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


def segment(key, sid, my_ids=()):
    """The same sentence again by its ID, or None when Nadeshiko no longer has it."""
    seg = _post(key, None, SEGMENT_URL + sid)
    if not seg.get("publicId") or not (seg.get("urls") or {}).get("videoUrl"):
        return None
    return _candidate(seg, {}, set(my_ids))


def field_html(c):
    """The Media field value. Keep in step with mediaHtml in index.html."""
    return f'[sound:{c["video_file"]}]<br>{c["ja_html"]}<br>{html.escape(c["en"], quote=False).replace('"', "&quot;")}'
