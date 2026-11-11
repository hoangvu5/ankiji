import copy
import json
import os
import threading
import uuid
from typing import List, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import anki, extract, llm, media, nadeshiko, notetypes
from .prompts import DEFAULT_PROMPTS, JAPANESE
from .paths import STATIC_DIR, data

SETTINGS_PATH = data("settings.json")

OLD_PLACEHOLDER = "(Placeholder - replace this with your own default prompt."

DEFAULTS = {
    "provider": "openai",
    "openai_api_key": "",
    "openai_model": "gpt-5.4-mini",
    "codex_model": "",
    "claude_model": "",
    "prompts": DEFAULT_PROMPTS,
    "active_prompt": DEFAULT_PROMPTS[0]["name"],
    "delivery": "ankiconnect",
    "deck": "Default",
    "model": "Basic",
    "manual_fields": "Front, Back",
    "direction": "forward",
    "dup_scope": "deck",
    "pdf_mode": "auto",
    "theme": "system",
    "nadeshiko_api_key": "",
    "mal_profile": "",
    "media_field": "Media",
    "media_search_field": "",
    "media_categories": ["ANIME"],
    "media_fallback": True,
}

RESET_GROUPS = {
    "anki": ["delivery", "deck", "model", "manual_fields", "direction", "dup_scope"],
    "ai": ["provider", "openai_model", "codex_model", "claude_model"],
    "prompt": ["prompts", "active_prompt"],
    "key": ["openai_api_key"],
    "display": ["theme", "pdf_mode"],
    "nadeshiko": ["nadeshiko_api_key", "mal_profile"],
    "media": ["media_field", "media_search_field", "media_categories", "media_fallback"],
}

_lock = threading.Lock()
IMAGES = {}  # id -> {"name", "b64"}


def load_settings():
    s = copy.deepcopy(DEFAULTS)
    saved = {}
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            pass
    s.update(saved)
    if "prompts" not in saved:  # settings from before named prompts: keep a custom single prompt
        old = (saved.get("default_prompt") or "").strip()
        if old and not old.startswith(OLD_PLACEHOLDER) and old != JAPANESE.strip():
            s["prompts"].append({"name": "My prompt", "text": old})
            s["active_prompt"] = "My prompt"
    s.pop("default_prompt", None)
    if not s["prompts"]:
        s["prompts"] = copy.deepcopy(DEFAULT_PROMPTS)
    if s["active_prompt"] not in [p["name"] for p in s["prompts"]]:
        s["active_prompt"] = s["prompts"][0]["name"]
    return s


def save_settings(s):
    with _lock, open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def public(s):
    p = {k: v for k, v in s.items() if k not in ("openai_api_key", "nadeshiko_api_key")}
    key = s.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    p["openai_key_hint"] = f"…{key[-4:]}" if key else ""
    nade = s.get("nadeshiko_api_key", "")
    p["nadeshiko_key_hint"] = f"…{nade[-4:]}" if nade else ""
    return p


app = FastAPI(title="Anki Card Maker")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/settings")
def get_settings():
    return public(load_settings())


@app.post("/api/settings")
def set_settings(data: dict = Body(...)):
    s = load_settings()
    for k, v in data.items():
        if k in ("openai_api_key", "nadeshiko_api_key") and not v:
            continue  # blank field = keep existing key
        if k == "prompts":
            v = [{"name": str(p.get("name", "")).strip()[:80], "text": str(p.get("text", ""))}
                 for p in v if isinstance(p, dict) and str(p.get("name", "")).strip()]
            if not v:
                continue
        if k in DEFAULTS:
            s[k] = v
    if data.get("clear_openai_key"):
        s["openai_api_key"] = ""
    save_settings(s)
    return public(s)


@app.post("/api/settings/reset")
def reset_settings(data: dict = Body(...)):
    s = load_settings()
    for g in data.get("groups", []):
        for k in RESET_GROUPS.get(g, []):
            s[k] = copy.deepcopy(DEFAULTS[k])
    save_settings(s)
    return public(s)


@app.get("/api/provider/check")
def provider_check(provider: str):
    return llm.check_provider(provider, load_settings())


@app.post("/api/extract")
async def do_extract(files: List[UploadFile] = File(default=[]), pasted: str = Form(""), pdf_mode: str = Form("auto")):
    texts, images, warnings = [], [], []
    if pasted.strip():
        texts.append(pasted.strip())
    for f in files:
        data = await f.read()
        t, im, w = extract.extract_file(f.filename or "file", data, pdf_mode)
        texts += t
        images += im
        warnings += w
    out_imgs = []
    for im in images:
        iid = uuid.uuid4().hex
        IMAGES[iid] = im
        out_imgs.append({"id": iid, "name": im["name"], "thumb": extract.make_thumb(im["b64"])})
    return {"text": "\n\n".join(texts), "images": out_imgs, "warnings": warnings}


@app.post("/api/generate")
def do_generate(data: dict = Body(...)):
    s = load_settings()
    provider = data.get("provider") or s["provider"]
    fields = [f for f in (data.get("fields") or []) if f] or ["Front", "Back"]
    images = [IMAGES[i] for i in data.get("image_ids", []) if i in IMAGES]
    instructions = data.get("instructions", "")
    # The media field is filled later from Nadeshiko, so the AI must leave it alone.
    skip = data.get("media_field") if data.get("media_field") in fields[1:] else None
    if skip:
        instructions += f'\n\nAlways leave the field "{skip}" as an empty string. The app fills it later.'
    try:
        cards = llm.generate(provider, s, fields, instructions, data.get("text", ""), images)
    except Exception as e:
        raise HTTPException(400, str(e))
    if skip:
        j = fields.index(skip)
        for c in cards:
            c["fields"][j] = ""
    return {"cards": cards, "fields": fields}


@app.get("/api/anki/status")
def anki_status():
    return anki.status()


@app.get("/api/anki/fields")
def anki_fields(model: str, direction: str = "forward"):
    try:
        models = anki.invoke("modelNames")
        used, _ = anki.resolve([], model, direction, models)
        try:
            templates = list(anki.invoke("modelTemplates", modelName=model).keys())
        except anki.AnkiError:
            templates = []
        return {"fields": anki.fields_for(model), "send_as": used, "templates": templates}
    except anki.AnkiError as e:
        raise HTTPException(400, str(e))


@app.get("/api/notetypes")
def notetype_list():
    return notetypes.public()


@app.get("/api/notetypes/handwriting")
def notetype_handwriting():
    return FileResponse(notetypes.handwriting_path(), media_type="application/javascript",
                        filename=notetypes.HANDWRITING_FILE)


@app.post("/api/anki/create-notetype")
def anki_create_notetype(data: dict = Body(...)):
    kind = data.get("kind")
    if kind not in notetypes.NOTE_TYPES:
        raise HTTPException(400, "Unknown note type.")
    try:
        return {"message": notetypes.create(kind, anki.invoke), "name": notetypes.NOTE_TYPES[kind]["name"]}
    except anki.AnkiError as e:
        raise HTTPException(400, str(e))


@app.post("/api/media/find")
def media_find(data: dict = Body(...)):
    s = load_settings()
    term = media.search_term(data.get("text", "")) if data.get("clean", True) else (data.get("text") or "").strip()
    try:
        out = media.find(s["nadeshiko_api_key"], term, data.get("categories") or [], nadeshiko.my_ids(), bool(data.get("fallback")))
    except media.MediaError as e:
        raise HTTPException(400, str(e))
    return {"term": term, **out}


@app.get("/api/media/status")
def media_status():
    s = load_settings()
    return {"has_key": bool(s["nadeshiko_api_key"]), "my_anime": len(nadeshiko.my_ids())}


def _args(d):
    return d["cards"], d.get("deck") or "Default", d.get("model") or "Basic", d.get("direction", "forward"), d.get("dup_scope", "deck")


@app.post("/api/anki/check")
def anki_check(data: dict = Body(...)):
    try:
        return anki.check(*_args(data))
    except anki.AnkiError as e:
        raise HTTPException(400, str(e))


@app.post("/api/anki/add")
def anki_add(data: dict = Body(...)):
    try:
        return anki.add(*_args(data), media_files=data.get("media_files"))
    except anki.AnkiError as e:
        raise HTTPException(400, str(e))


@app.post("/api/export/tsv")
def export_tsv(data: dict = Body(...)):
    cards, deck, model, direction, _ = _args(data)
    n = max([len(data.get("fields") or [])] + [len(c["fields"]) for c in cards] + [2])
    _, text = anki.to_tsv(cards, deck, model, direction, n)
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in deck).strip() or "cards"
    return Response(text.encode("utf-8"), media_type="text/tab-separated-values; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-anki-import.txt"'})


@app.get("/api/nadeshiko/state")
def nadeshiko_state():
    return nadeshiko.view()


@app.post("/api/nadeshiko/sync")
def nadeshiko_sync():
    s = load_settings()
    if not s["mal_profile"].strip():
        raise HTTPException(400, "Paste your MyAnimeList profile link first.")
    if not nadeshiko.start_sync(s["nadeshiko_api_key"], s["mal_profile"]):
        raise HTTPException(409, "An update is already running.")
    return nadeshiko.view()


@app.post("/api/nadeshiko/search-again")
def nadeshiko_search_again(data: dict = Body(...)):
    s = load_settings()
    if not s["nadeshiko_api_key"]:
        raise HTTPException(400, "Add your Nadeshiko API key first.")
    if not nadeshiko.search_again(s["nadeshiko_api_key"], data["id"]):
        raise HTTPException(409, "An update is already running.")
    return nadeshiko.view()


@app.post("/api/nadeshiko/use")
def nadeshiko_use(data: dict = Body(...)):
    nadeshiko.set_use(data["id"], data["use"])
    return nadeshiko.view()
