# Ankiji - project notes for AI agents

An open-source (MIT) Windows app that turns study material (PDFs, photos, Word, PowerPoint, text) into Anki flashcards with an AI, sends them into Anki without duplicates, and adds anime example sentences from Nadeshiko. Started for Japanese kanji/vocab study (e.g. 3rd-grade kanji worksheets with vertical text and furigana); a Mandarin prompt and note type exist too. Users are often not technical. Contributor guide: CONTRIBUTING.md.

## Project rules (follow these)

- **Never use em dashes or en dashes** in UI text, messages, README or replies. Use "-", commas, colons or rewording.
- Windows only: Windows Python, PowerShell, Edge app window. Programs installed inside WSL are invisible to the app.
- Keep it simple to use: the default path should be the easiest one, and UI text short and plain.
- Plan first and ask questions when a request is ambiguous; otherwise just do it.
- When explaining things, be short and practical. If asked not to code, only explain.
- Conventional Commits, one branch and PR per change. Never commit settings.json, caches, keys or personal data.

## Maintainer's machine

- C: is low on space; the project lives on D: at `D:\VSCode\AI\anki-card-maker` (the GitHub repo may be renamed to ankiji).

## Run

- Double-click `start.bat` (creates `.venv`, installs `requirements.txt` once, then runs `run.py`).
- `run.py` starts uvicorn on **http://127.0.0.1:8770** (in a thread, app object imported directly) and opens an **app window**: Edge (or Chrome) with `--app=URL --user-data-dir=<data>/browser-profile`. Its own profile makes the window its own process; when it closes, the server stops. Already running = just open another window. No Edge/Chrome = normal browser and keep serving. `run.py --browser` = normal browser. Env `ANKIJI_PORT` overrides the port (used for testing the .exe while the user's own copy holds 8770).
- Windowed .exe has no console: `run.py` redirects stdout/stderr to `app-log.txt` and shows errors in a MessageBox. `llm.py` passes `CREATE_NO_WINDOW` to every subprocess so Codex/Claude don't flash consoles.

## Packaging (desktop app for a low-tech friend)

- `build.bat` -> PyInstaller **onedir**, `--windowed`, icon `assets/icon.ico`, static files added as `app/static`. Output `dist\Ankiji\Ankiji.exe` and `dist\Ankiji.zip` (~60 MB). It deletes settings.json, nadeshiko_cache.json, app-log.txt and browser-profile from the dist folder so the user's key never ships, and copies README.md, LICENSE (as LICENSE.txt) and THIRD_PARTY_NOTICES.md in, because MIT and CC BY-SA need their notices shipped with copies.
- Dependencies are pinned exactly: app packages in `requirements.txt`, PyInstaller in `requirements-build.txt` (which includes the first; `build.bat` installs it). `.github/dependabot.yml` opens grouped PRs for minor/patch pip bumps weekly and Actions bumps monthly, with `build:` / `ci:` commit prefixes. `.github/workflows/dependabot-automerge.yml` merges Dependabot PRs whose update-type (from dependabot/fetch-metadata; for groups the biggest one) is minor or patch, then runs `gh workflow run release.yml`, because a merge made with GITHUB_TOKEN does not trigger the push workflow. Majors wait for a person; PRs are not built before merging, but the release build fails (and keeps the old release) if a bump breaks the build.
- GitHub (private repo): `.github/workflows/release.yml` runs `build.bat` on `windows-latest` (Python 3.14) on every push to main and replaces the release tagged `latest` with the new `Ankiji.zip`. `build.bat` checks `CI` (set by GitHub): uses plain `python` and a no-op instead of `pause` (NOT `rem`: it would comment out `exit /b 1)` and break parsing). `.gitattributes` forces CRLF for .bat files.
- `.gitignore` keeps out everything personal: settings.json (API keys, MAL username), nadeshiko_cache.json, browser-profile/, logs, .venv, build/, dist/, .claude/.
- Chose Edge app mode over pywebview: no extra deps (pythonnet on Python 3.14 was a risk), downloads (TSV) and clipboard just work.
- `app/paths.py`: `STATIC_DIR` (bundle, `sys._MEIPASS`) and `DATA_DIR` (next to the .exe, or the project root from source; falls back to `%LOCALAPPDATA%\Ankiji` if the folder isn't writable). All runtime files go through `paths.data()`.
- Tested: exe starts, serves fresh settings, closing the window ends the process. Not yet tested on another PC (SmartScreen / antivirus warnings expected, README covers them).
- Port 8765 is reserved for AnkiConnect, so never use it for the app.
- Reinstall deps after changing requirements: delete `.venv\installed.flag` and run `start.bat`.

## Files

```
start.bat            Windows launcher (finds py/python, venv, pip install, runs run.py)
run.py               uvicorn + open browser
requirements.txt     fastapi, uvicorn, python-multipart, pymupdf, python-docx, python-pptx, pillow, pillow-heif, openai, requests
settings.json        created at runtime; holds all saved choices AND the OpenAI API key. Never commit it (add to .gitignore).
app/server.py        FastAPI routes, settings load/save/reset, in-memory image store
app/extract.py       file -> text + images
app/llm.py           card generation via 3 providers, chunking, de-dupe
app/nadeshiko.py     MAL list reading, Nadeshiko search, cache, background job (Nadeshiko tab)
app/media.py         example sentences (image + audio + JA/EN subs) from Nadeshiko for the Media field
app/paths.py         bundle vs data folders (source run and packaged .exe)
app/prompts.py       built-in card prompts (the user's Japanese one, and a Mandarin one for a Vietnamese HSK 1-2 learner)
app/notetypes.py     ready-made note types (Japanese Card Format, Mandarin Card Format): fields, templates, CSS, one-click create
app/static/_kanjicanvas.js  KanjiCanvas handwriting lib + patterns (MIT), copied from the user's Anki media folder
build.bat            builds the .exe and dist\Ankiji.zip
assets/icon.ico      app icon (made with Pillow)
nadeshiko_cache.json created at runtime; every anime already searched, plus the user's ticks
app/anki.py          AnkiConnect calls, duplicate check, direction logic, TSV export
app/static/index.html  the whole UI (vanilla HTML/CSS/JS, no build step)
README.md            user-facing guide
```

## How it works

1. **Extract** (`POST /api/extract`): PDFs via PyMuPDF. PDF mode `auto` uses page text if a page has >= 40 chars, otherwise renders the page to an image; `images` always renders; `text` text only. Photos are EXIF-rotated, shrunk to 2000 px long side, JPEG q85. Images are kept in a server-side dict (`IMAGES`, by uuid) and the UI gets thumbnails + ids. Scanned worksheets have no text layer, so they go to the AI as images (vision). This was a deliberate choice: OCR handles vertical Japanese + furigana badly.
2. **Generate** (`POST /api/generate`): text is chunked (12k chars) and images grouped (6 per call); each part is one AI call, run in a thread pool; results merged and de-duped by normalized first field.
   - **Cancel**: the page sends a `job_id`; while running, the Generate button becomes Cancel, which aborts the fetch and calls `POST /api/generate/cancel`. `llm.Job` kills CLI processes with `taskkill /T /F` (codex.cmd starts node, which starts the binary) and closes the OpenAI client; parts not started yet are skipped, and the route answers 409 "Cancelled.". Tested with a slow fake CLI: stopped in under 2 s, no child left.
   - The AI fills the **actual fields of the chosen Anki note type**. Fields are passed as `f0..fN` keys (safe JSON keys) with a mapping in the system prompt; a strict JSON schema is built per request.
   - The system prompt tells the AI the first field is the duplicate key and must be short and consistently formatted.
   - The user's card prompt decides content/format. Default prompt is a placeholder the user will replace.
3. **Review** in a table (editable cells, tags, include checkbox, status badge, optional HTML preview).
4. **Send**: AnkiConnect (default) or download a TSV import file.

## AI providers (dropdown "AI")

| value | what | notes |
|---|---|---|
| `openai` | OpenAI API, Chat Completions with `response_format: json_schema` (strict) and image `data:` URLs | key from settings or `OPENAI_API_KEY`. Default model `gpt-5.4-mini` (user wanted cheap but capable). Don't send `temperature` (reasoning models reject it). |
| `codex` | user's **ChatGPT subscription** via Codex CLI `codex exec` | `-i img` flags go FIRST (the flag can swallow later args), then `--skip-git-repo-check --sandbox read-only --output-schema schema.json -o out.json [-m model] -`, prompt via stdin (avoids Windows .cmd quoting problems). Strips OPENAI_API_KEY/CODEX_API_KEY from env so the ChatGPT login is used. |
| `claude` | user's **Claude subscription** via Claude Code `claude -p --input-format stream-json --output-format stream-json --verbose --max-turns 2` | one stream-json user message with base64 image blocks + text (system text is put inside the message, not as a CLI arg, for Windows quoting safety). Reads the `type: result` event; uses `structured_output` if present else parses JSON from the text. Strips ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN so the subscription login is used. |

Windows CLI detection (`_search_path` in llm.py): PATH is **re-read from the registry on every check** (HKCU + HKLM Environment) plus common install dirs (`%APPDATA%\npm`, `%USERPROFILE%\.local\bin`, Codex/WinGet dirs). Reason: tools installed after the app started were not detected. The UI has a "Check again" link. Codex check also runs `codex login status` and reports "installed but not signed in".

Install notes for the user (Windows):
- Codex: `irm https://chatgpt.com/codex/install.ps1 | iex` FAILS in old Windows PowerShell 5.1 ("property 'OSArchitecture' cannot be found"). Use `npm install -g @openai/codex` (needs Node.js) or run the installer in PowerShell 7 (`winget install Microsoft.PowerShell`). Then `codex login` -> Sign in with ChatGPT. As of the last session, Codex was still being installed on Windows (earlier it was only installed in WSL, which the app can't see).
- Claude Code: `irm https://claude.ai/install.ps1 | iex`, then run `claude` once to log in (Git for Windows is optional since 2026).

## Anki

- Uses the **AnkiConnect** add-on (code `2055492159`) at `http://127.0.0.1:8765`, API version 6. Calls are made from the Python backend (no CORS issues).
- On load and on "Recheck" the UI fetches `deckNames`, `modelNames`; on note type change it fetches `modelFieldNames` and `modelTemplates` (number of cards per note).
- **Deck picker is a `<select>`** of all decks plus "+ New deck..." (shows a text input). It used to be an input+datalist, which hid other decks because the browser filters suggestions by the typed value. New decks are created with `createDeck` on send.
- **Duplicates**: Anki compares the **first field** within the same note type. Check uses `canAddNotesWithErrorDetail` (fallback `canAddNotes`); add uses `addNote` one by one with `allowDuplicate: false` and `duplicateScope` = deck (with `duplicateScopeOptions.deckName`) or collection (user choice). Within-batch repeats are flagged too. Duplicates are unticked automatically and re-checked whenever deck / note type / direction / scope changes and after sending.
- **Card direction** (important, the user asked what it means): which cards Anki creates is decided by the note type's templates, not by the app.
  - `forward` "Use the note type's own cards" = default and recommended. The user's own custom note type already has 2 templates (both directions), so this gives both cards.
  - `backward` "Swap the first two fields" = swaps field 1 and 2 on send.
  - `both` "Add reverse cards (Basic only)" = sends "Basic" notes as "Basic (and reversed card)". Disabled in the UI when the selected note type has 2+ templates.
- Without AnkiConnect, the user types the note type name and its field names (comma-separated), and gets a TSV with headers `#separator:tab`, `#html:true`, `#notetype:`, `#deck:`, `#tags column:N`. Tell them to set **Existing notes = Preserve** in File > Import.

## Settings (settings.json)

- Everything autosaves (debounced) and reloads next session: provider, delivery, deck, note type, manual fields, direction, duplicate scope, PDF mode, card prompts (`prompts` + `active_prompt`, saved as you type), theme, model names, OpenAI key. "Extra instructions for this batch" is intentionally NOT saved.
- API keys (OpenAI, Nadeshiko) are never sent back to the browser; only `*_key_hint` (last 4 chars) tells the page one is saved. A saved key shows as dots: the field holds the stand-in `KEY_MASK`, and sending that (or blank) back means keep the saved key.
- **Reset settings** button opens a `<dialog>` with groups (`RESET_GROUPS` in server.py): `anki`, `ai`, `prompt`, `key`, `display`. `POST /api/settings/reset {groups:[...]}` restores DEFAULTS for those keys.

## UI

- Single page, sections: 1 Material, 2 Prompt, 3 Destination, 4 Generation, 5 Review, then send. The "setup guide for this AI" link under the AI dropdown only shows when the provider check fails. AI settings in a slide-out drawer.
- **Theme**: System (default, follows Windows) / Light / Dark, via CSS variables on `:root`, dark under `prefers-color-scheme` guarded by `:root:not([data-theme="light"])` and under `:root[data-theme="dark"]`. Saved in settings and cached in localStorage (applied in `<head>` to avoid a flash).
- Paste a screenshot anywhere (Ctrl+V) to add it as material.
- Generating again asks whether to append to or replace the cards in the review list.

## Nadeshiko tab

- Reads Watching (status 1) and Completed (status 2) from MAL's public `myanimelist.net/animelist/{user}/load.json`, paged by `offset` until an empty page. No MAL key. A private or missing list returns 400.
- Nadeshiko: `POST https://api.nadeshiko.co/v1/search/media` `{query, take}` with a Bearer key. Limits: 150 requests a minute, 5,000 a month, which is why the cache exists.
- Each update: fetch MAL, drop cached anime no longer listed, then search only entries with `searched: false`. A failed or empty MAL read changes nothing. Errors and 5xx leave an entry unsearched for retry; "not on Nadeshiko" is cached (per-row "Search again" overrides).
- Runs in a background thread; the page polls `GET /api/nadeshiko/state`. The cache is saved after each anime.
- **Not yet verified with a real Nadeshiko key.** Result fields (`media[].publicId`, `nameRomaji`, `nameEn`, `nameJa`) come from the user's original HTML tool. The MAL side was tested against the real site.

## Media from Nadeshiko ("Fill media" above the review table)

- `POST https://api.nadeshiko.co/v1/search` with `query {search, exactMatch}`, `filters {category, contentRating [SAFE, SUGGESTIVE], languages: [] (match Japanese only, otherwise "xyz" matched English subs), media.include}`, `include: ["media"]`, `take 10`. Segment has `textJa.content/highlight` (highlight uses `<em>` and can contain other spans: strip all tags, keep em as `<b>`), `textEn.content/isMachineTranslated`, `urls.imageUrl/audioUrl` (public CDN `cdn.nadeshiko.co`, webp + mp3, no auth). Full spec is embedded in nadeshiko.co/docs/api (redoc `__redoc_definition`).
- Steps: exact search in "my anime" (ticked rows in the Nadeshiko tab, max 100 include ids) if ANIME is a chosen kind; then, if fallback is on (or no anime ticked), everything with `preferMedia` = my ids; stop at 3 results. If nothing, repeat with `exactMatch: false`. Every line must contain the word's stem (drops unrelated fuzzy hits). Human subtitles and short lines sorted first.
- The search word comes from a chosen field via `media.search_term` (strips HTML, `[furigana]`, `(readings)`, takes the first of a list).
- Field value: `<img src="nadeshiko_<segId>.webp"> [sound:nadeshiko_<segId>.mp3]<br>JA<br>EN`. The browser keeps the candidates per card (Next sentence cycles without a new request). On send the page passes `media_files {filename: cdn url}`; `anki.add` stores only files used by the notes via `storeMediaFile {filename, url}` (only cdn.nadeshiko.co URLs). TSV export has no media.
- Generate passes `media_field`; the server tells the AI to leave it empty and blanks it afterwards.
- Settings: `media_field` (default "Media"), `media_search_field` ("" = first), `media_categories` (["ANIME"]), `media_fallback` (true). Reset group `media`.
- Tested with the real key: 練習, 食べた, 走る found in the user's anime; nonsense words return nothing. Send path tested with a fake AnkiConnect only (real Anki was open, not written to).

## Named prompts

- Settings `prompts: [{name, text}]` + `active_prompt`; defaults ("Japanese", "Mandarin") come from `app/prompts.py`. Reset group `prompt` restores the two built-ins. (Migrations from the old single `default_prompt` and the long prompt names were removed once the only old settings file had been converted.)
- Section 2 has a dropdown plus New (copy of current) / Rename / Delete (uses `prompt()` / `confirm()`). Generation uses the textarea, which shows the active prompt.

## Ready-made note types

- `notetypes.py` holds the user's Japanese templates (pasted by the user) with ONE change: "Get clip" calls the shareable Shortcut "Ankiji Nadeshiko" instead of the user's personal "Nadeshiko Clip"; "Browse" links to nadeshiko.co.
- Mandarin: same Card 1; Card 2 is meaning -> recall (no handwriting: KanjiCanvas only has Japanese kanji); no Nadeshiko link (Japanese only). CSS adds Chinese fonts.
- `POST /api/anki/create-notetype {kind}` -> `createModel` (skips if the name exists) and, for Japanese, `storeMediaFile _kanjicanvas.js`. `GET /api/notetypes` feeds the guide's copy boxes; `GET /api/notetypes/handwriting` downloads the JS for manual setup.
- Tested with a fake AnkiConnect only (did not create models in the user's real Anki).

## Setup guide tab

Step-by-step for low-tech users: AnkiWeb account + phone sync, Anki + AnkiConnect, note type (one-click or by hand with template copy boxes, and a note that an AI can customize templates), OpenAI API, ChatGPT (Node.js + `npm install -g @openai/codex` in **Command Prompt**, avoiding PowerShell's npm.ps1 execution-policy block), Claude (`irm https://claude.ai/install.ps1 | iex`, no Git needed), Nadeshiko. Copy buttons on commands. Section 4 shows "<AI> setup guide" next to Check again when the AI is not ready, which jumps to the selected provider's section.

## API routes

`GET /` page · `GET/POST /api/settings` · `POST /api/settings/reset` · `GET /api/provider/check?provider=` · `POST /api/extract` (multipart: files, pasted, pdf_mode) · `POST /api/generate` · `GET /api/anki/status` · `GET /api/anki/fields?model=` · `POST /api/anki/check` · `POST /api/anki/add` · `POST /api/export/tsv` · `GET /api/nadeshiko/state` · `POST /api/nadeshiko/sync` · `POST /api/nadeshiko/search-again` · `POST /api/nadeshiko/use` · `POST /api/media/find {text, clean, categories, fallback}` · `GET /api/media/status` · `GET /api/notetypes` · `GET /api/notetypes/handwriting` · `POST /api/anki/create-notetype`

## Testing approach used so far

- A fake AnkiConnect server (simple `http.server` on 8765 implementing version, deckNames, modelNames, modelFieldNames, modelTemplates, canAddNotesWithErrorDetail, createDeck, addNote) and fake `codex` / `claude` scripts on PATH, then Playwright driving the UI end to end (light + dark, persistence, reset, new deck, duplicates).
- **Not yet verified against the real services**: real OpenAI API call, real `codex exec` (esp. `-o`/`--output-schema` behaviour and `login status` output), real `claude -p` stream-json with images, and the user's real Anki/custom note type. If something fails, check these first.

## Ideas not built yet

- Progress per part during long generations (currently one request with an elapsed-time spinner).
- Sending existing deck fronts to the AI so it avoids generating cards that already exist.
- Auto-fill media right after generating (now it's a click).

## iPhone clips (Shortcut "Ankiji Nadeshiko")

- The Japanese note type's back shows "Get clip" (runs `shortcuts://run-shortcut?name=Ankiji Nadeshiko&input=text&text=<word>`; the user's own note type still calls their original "Nadeshiko Clip") and "Browse" when Media is empty. The Mandarin one has neither (Nadeshiko is Japanese only).
- The user's Shortcut (read from its iCloud link, never stored): Text = search JSON with the anime `include` list typed in, POST /v1/search take 1 with `Authorization: Bearer <key>` in the headers of 2 "Get contents of URL" actions; if no segment, same search without the filter; if still none, notify "No clip on Nadeshiko" and open `anki://`. Otherwise download `urls.videoUrl` as `<word>.mp4` to Files (On My iPhone), copy "\nJA\nEN" (tags stripped) to the clipboard, open `anki://`. The user attaches the video and pastes the subtitles in AnkiMobile.
- Sharing: the shared copy "Ankiji Nadeshiko" (`CLIP_SHORTCUT_LINK` in index.html, https://www.icloud.com/shortcuts/d20bacb214db4ce9b3b19e48302ebed1) starts with two Text actions, "API key" and "Anime JSON", holding placeholders; both requests use them as chips. No Import Questions (the user could not add them), so the guide says to replace the two boxes. Checked on 2026-11-11: no API key, no anime IDs inside. iCloud links are snapshots: re-share after edits. Check a link without a phone: `GET https://www.icloud.com/shortcuts/api/records/<id>` gives `fields.shortcut.value.downloadURL`, an unsigned binary plist (plistlib); search the parsed values, because non-ASCII strings are UTF-16 in the file.
