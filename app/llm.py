"""Card generation through three backends:
- openai : OpenAI API (pay per use, needs an API key)
- codex  : OpenAI Codex CLI signed in with your ChatGPT subscription
- claude : Claude Code CLI signed in with your Claude subscription
"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

TEXT_CHUNK = 12000   # characters of text per AI call
IMAGES_PER_CALL = 6
CLI_TIMEOUT = 900    # seconds
# No black console window flashing up when the app runs as a windowed .exe.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

SYSTEM = """You turn study material into Anki notes.

Each note has these fields, in order:
{field_list}

Rules:
- Follow the user's card instructions exactly; they decide what the cards contain and how they look.
- The FIRST field ({first}) is the card's unique key. Keep it short and formatted the same way on every
  note (no extra decoration), because Anki detects duplicates by comparing this field.
- Do not create two notes with the same first field.
- Fields may use simple HTML (<br>, <b>, <i>, <ruby>). Never use Markdown.
- Leave a field as an empty string if it doesn't apply.
- "tags": short lowercase tags without spaces (use_underscores), or an empty list.
- Only use information from the material provided. If the material contains nothing worth a card, return no cards.
"""


def schema_for(n_fields: int) -> dict:
    props = {f"f{i}": {"type": "string"} for i in range(n_fields)}
    props["tags"] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props.keys()),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["cards"],
        "additionalProperties": False,
    }


def _system(fields):
    field_list = "\n".join(f'- "f{i}" = {name}' for i, name in enumerate(fields))
    return SYSTEM.format(field_list=field_list, first=f'"f0" = {fields[0]}')


def _user_text(instructions, text, part_label):
    out = f"CARD INSTRUCTIONS:\n{instructions.strip() or 'Make clear, atomic flashcards of the key facts.'}\n"
    if part_label:
        out += f"\n(This is {part_label} of the material.)\n"
    if text:
        out += f"\nMATERIAL (text):\n{text}\n"
    else:
        out += "\nMATERIAL: see the attached image(s).\n"
    return out


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if m:
        raw = m.group(1)
    else:
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e == -1:
            raise RuntimeError("The AI did not return JSON. Its answer began: " + raw[:300])
        raw = raw[s : e + 1]
    return json.loads(raw)


# ---------------- providers ----------------

def _call_openai(settings, fields, instructions, text, images, label):
    from openai import OpenAI

    key = settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("No OpenAI API key. Add it in Settings, or pick a subscription option instead.")
    client = OpenAI(api_key=key)
    content = [{"type": "text", "text": _user_text(instructions, text, label)}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img['b64']}", "detail": "high"}})
    resp = client.chat.completions.create(
        model=settings.get("openai_model") or "gpt-5.4-mini",
        messages=[{"role": "system", "content": _system(fields)}, {"role": "user", "content": content}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "anki_cards", "strict": True, "schema": schema_for(len(fields))},
        },
    )
    msg = resp.choices[0].message
    if getattr(msg, "refusal", None):
        raise RuntimeError("The model refused: " + msg.refusal)
    return json.loads(msg.content)


def _search_path():
    """PATH as it is right now. On Windows, re-read it from the registry so tools
    installed after this app started (Codex, Claude Code) are found without a restart."""
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if os.name == "nt":
        import winreg

        for root, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    parts += os.path.expandvars(val).split(os.pathsep)
            except OSError:
                pass
        parts += [os.path.expandvars(p) for p in (
            r"%APPDATA%\npm", r"%USERPROFILE%\.local\bin", r"%LOCALAPPDATA%\Programs\OpenAI\Codex\bin",
            r"%USERPROFILE%\.codex\bin", r"%LOCALAPPDATA%\Microsoft\WinGet\Links")]
    seen, out = set(), []
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return os.pathsep.join(out)


def _find_cli(name):
    return shutil.which(name, path=_search_path())


def _cli_env(drop):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PATH"] = _search_path()
    return env


def _call_codex(settings, fields, instructions, text, images, label):
    exe = _find_cli("codex")
    if not exe:
        raise RuntimeError("Codex CLI not found. Install it, run `codex login`, choose 'Sign in with ChatGPT', then try again.")
    with tempfile.TemporaryDirectory() as tmp:
        schema_path = os.path.join(tmp, "schema.json")
        out_path = os.path.join(tmp, "out.json")
        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(schema_for(len(fields)), f)
        cmd = [exe, "exec"]
        for i, img in enumerate(images):  # images first so the flag can't swallow later args
            p = os.path.join(tmp, f"img{i}.jpg")
            with open(p, "wb") as f:
                f.write(base64.b64decode(img["b64"]))
            cmd += ["-i", p]
        cmd += ["--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", schema_path, "-o", out_path]
        if settings.get("codex_model"):
            cmd += ["-m", settings["codex_model"]]
        cmd.append("-")  # prompt comes from stdin
        prompt = _system(fields) + "\n\n" + _user_text(instructions, text, label) + \
            "\nDo not run commands or edit files. Reply only with the JSON object."
        env = _cli_env(("OPENAI_API_KEY", "CODEX_API_KEY"))
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=tmp, timeout=CLI_TIMEOUT, env=env, creationflags=NO_WINDOW)
        if os.path.exists(out_path) and os.path.getsize(out_path):
            with open(out_path, encoding="utf-8") as f:
                return _parse_json(f.read())
        tail = (r.stderr or r.stdout or "").strip()[-800:]
        if "login" in tail.lower() or "auth" in tail.lower():
            tail += "\n\nTip: open a terminal and run `codex login`, then choose Sign in with ChatGPT."
        raise RuntimeError("Codex failed:\n" + tail)


def _call_claude(settings, fields, instructions, text, images, label):
    exe = _find_cli("claude")
    if not exe:
        raise RuntimeError("Claude Code not found. Install it, run `claude` once and log in with your Claude account, then try again.")
    content = []
    for img in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img["b64"]}})
    content.append({"type": "text", "text": _system(fields) + "\n\n" + _user_text(instructions, text, label) +
                    "\nDo not use any tools. Reply with ONLY one JSON object of the form "
                    '{"cards":[{"f0":"...","f1":"...","tags":[]}]} and nothing else.'})
    line = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
    cmd = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--max-turns", "2"]
    if settings.get("claude_model"):
        cmd += ["--model", settings["claude_model"]]
    # Make sure the subscription login is used, not an API key that happens to be set.
    env = _cli_env(("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(cmd, input=line, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=tmp, timeout=CLI_TIMEOUT, env=env, creationflags=NO_WINDOW)
    result = None
    for ln in r.stdout.splitlines():
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if ev.get("type") == "result":
            result = ev
    if not result:
        raise RuntimeError("Claude Code failed:\n" + (r.stderr or r.stdout).strip()[-800:])
    if result.get("is_error"):
        raise RuntimeError("Claude Code error: " + str(result.get("result") or result.get("subtype")))
    if isinstance(result.get("structured_output"), dict):
        return result["structured_output"]
    return _parse_json(result.get("result") or "")


PROVIDERS = {"openai": _call_openai, "codex": _call_codex, "claude": _call_claude}


def check_provider(provider, settings):
    if provider == "openai":
        ok = bool(settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY"))
        return {"ok": ok, "detail": "API key set" if ok else "No API key yet. Add one in AI settings."}
    exe = _find_cli(provider)
    if not exe:
        hint = {"codex": "Install Codex CLI, then run `codex login` → Sign in with ChatGPT.",
                "claude": "Install Claude Code, then run `claude` once and log in with your Claude account."}[provider]
        return {"ok": False, "detail": "Not installed. " + hint}
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, env=_cli_env(()), creationflags=NO_WINDOW).stdout.strip()
    except Exception as e:
        return {"ok": False, "detail": f"Found at {exe}, but it wouldn't run ({e})."}
    if provider == "codex":
        try:
            r = subprocess.run([exe, "login", "status"], capture_output=True, text=True, timeout=30, env=_cli_env(()), creationflags=NO_WINDOW)
            msg = (r.stdout + r.stderr).strip()
            if "not logged in" in msg.lower() or "not signed in" in msg.lower():
                return {"ok": False, "detail": f"Installed ({v}) but not signed in. Run `codex login` and choose Sign in with ChatGPT."}
            return {"ok": True, "detail": f"Ready - {v}. {msg.splitlines()[0] if r.returncode == 0 and msg else ''}".strip()}
        except Exception:
            pass
    return {"ok": True, "detail": f"Ready - {v}".strip()}


# ---------------- orchestration ----------------

def _chunks(text, size):
    text = text.strip()
    if not text:
        return []
    out, cur = [], ""
    for para in re.split(r"(\n\s*\n)", text):
        if len(cur) + len(para) > size and cur.strip():
            out.append(cur)
            cur = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        cur += para
    if cur.strip():
        out.append(cur)
    return out


def generate(provider, settings, fields, instructions, text, images):
    if provider not in PROVIDERS:
        raise RuntimeError(f"Unknown provider {provider}")
    parts = [(c, []) for c in _chunks(text, TEXT_CHUNK)]
    parts += [("", images[i : i + IMAGES_PER_CALL]) for i in range(0, len(images), IMAGES_PER_CALL)]
    if not parts:
        raise RuntimeError("Nothing to make cards from. Add files or text first.")
    fn = PROVIDERS[provider]
    labels = [f"part {i + 1} of {len(parts)}" if len(parts) > 1 else "" for i in range(len(parts))]
    workers = 3 if provider == "openai" else 2
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda a: fn(settings, fields, instructions, a[0][0], a[0][1], a[1]),
                                zip(parts, labels)))
    cards, seen = [], set()
    for res in results:
        for c in res.get("cards", []):
            vals = [str(c.get(f"f{i}", "") or "").strip() for i in range(len(fields))]
            key = re.sub(r"\s+", " ", vals[0]).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            tags = [re.sub(r"\s+", "_", str(t).strip()) for t in (c.get("tags") or []) if str(t).strip()]
            cards.append({"fields": vals, "tags": tags})
    return cards
