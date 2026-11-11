"""Talk to Anki desktop through the AnkiConnect add-on, or build an import file."""
import re

import requests

from .media import files_to_store

ANKI_URL = "http://127.0.0.1:8765"
REVERSED = "Basic (and reversed card)"


class AnkiError(RuntimeError):
    pass


def invoke(action, **params):
    try:
        r = requests.post(ANKI_URL, json={"action": action, "version": 6, "params": params}, timeout=15)
    except requests.RequestException:
        raise AnkiError("Can't reach Anki. Open Anki desktop and make sure the AnkiConnect add-on is installed.")
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or "result" not in data or "error" not in data:
        raise AnkiError(
            "Something else is using port 8765, not AnkiConnect. Close that program "
            "(or move it to another port), then restart Anki."
        )
    if data.get("error"):
        raise AnkiError(str(data["error"]))
    return data.get("result")


def status():
    try:
        version = invoke("version")
    except AnkiError as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "version": version, "decks": sorted(invoke("deckNames")), "models": sorted(invoke("modelNames"))}


def fields_for(model):
    return invoke("modelFieldNames", modelName=model)


def resolve(cards, model, direction, model_names=None):
    """Apply the direction choice. Returns (model_to_use, list_of_field_lists)."""
    rows = [list(c["fields"]) for c in cards]
    if direction == "backward":
        for r in rows:
            if len(r) >= 2:
                r[0], r[1] = r[1], r[0]
    if direction == "both" and model == "Basic" and (model_names is None or REVERSED in model_names):
        model = REVERSED
    return model, rows


def _note(deck, model, field_names, values, tags, scope):
    opts = {"allowDuplicate": False, "duplicateScope": "deck" if scope == "deck" else "collection"}
    if scope == "deck":
        opts["duplicateScopeOptions"] = {"deckName": deck, "checkChildren": False, "checkAllModels": False}
    return {
        "deckName": deck,
        "modelName": model,
        "fields": {name: (values[i] if i < len(values) else "") for i, name in enumerate(field_names)},
        "tags": tags,
        "options": opts,
    }


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip().lower()


def check(cards, deck, model, direction, scope):
    """Label each card: new / duplicate / error."""
    models = invoke("modelNames")
    model, rows = resolve(cards, model, direction, models)
    if model not in models:
        raise AnkiError(f'Note type "{model}" doesn\'t exist in Anki.')
    names = fields_for(model)
    decks = invoke("deckNames")
    notes = [_note(deck, model, names, r, c.get("tags", []), scope) for r, c in zip(rows, cards)]
    if deck in decks:
        try:
            res = invoke("canAddNotesWithErrorDetail", notes=notes)
            out = []
            for item in res:
                if item.get("canAdd"):
                    out.append({"state": "new"})
                elif "duplicate" in (item.get("error") or "").lower():
                    out.append({"state": "duplicate"})
                else:
                    out.append({"state": "error", "detail": item.get("error")})
        except AnkiError:  # older AnkiConnect
            res = invoke("canAddNotes", notes=notes)
            out = [{"state": "new" if ok else "duplicate"} for ok in res]
    else:
        out = [{"state": "new"} for _ in notes]  # new deck: nothing to clash with
    seen = set()
    for i, r in enumerate(rows):
        k = _norm(r[0] if r else "")
        if out[i]["state"] == "new" and k in seen:
            out[i] = {"state": "batch_duplicate"}
        seen.add(k)
    return {"model": model, "fields": names, "results": out}


def add(cards, deck, model, direction, scope, media_files=None):
    models = invoke("modelNames")
    model, rows = resolve(cards, model, direction, models)
    names = fields_for(model)
    if deck not in invoke("deckNames"):
        invoke("createDeck", deck=deck)
    added, dupes, errors = 0, 0, []
    for fname, url in files_to_store(rows, media_files).items():
        try:  # Anki downloads the file into its media folder
            invoke("storeMediaFile", filename=fname, url=url)
        except AnkiError as e:
            errors.append(f"{fname}: {e}")
    for r, c in zip(rows, cards):
        try:
            invoke("addNote", note=_note(deck, model, names, r, c.get("tags", []), scope))
            added += 1
        except AnkiError as e:
            if "duplicate" in str(e).lower():
                dupes += 1
            else:
                errors.append(f"{(r[0] if r else '')[:40]}: {e}")
    return {"added": added, "skipped_duplicates": dupes, "errors": errors, "model": model}


def to_tsv(cards, deck, model, direction, n_fields):
    model, rows = resolve(cards, model, direction)

    def clean(v):
        return (v or "").replace("\t", " ").replace("\r", "").replace("\n", "<br>")

    lines = ["#separator:tab", "#html:true", f"#notetype:{model}", f"#deck:{deck}", f"#tags column:{n_fields + 1}"]
    for r, c in zip(rows, cards):
        vals = [clean(r[i] if i < len(r) else "") for i in range(n_fields)]
        lines.append("\t".join(vals + [" ".join(c.get("tags", []))]))
    return model, "\n".join(lines) + "\n"
