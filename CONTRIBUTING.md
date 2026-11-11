# Contributing to Ankiji

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Run it from source

You need Windows, [Python 3.12 or newer](https://www.python.org/downloads/) (3.14 is what the release is built with) and Anki with AnkiConnect for most features.

```bat
git clone https://github.com/hoangvu5/ankiji.git
cd ankiji
start.bat
```

`start.bat` creates `.venv`, installs `requirements.txt` the first time and runs `run.py`, which serves the app on http://127.0.0.1:8770 and opens it in an Edge app window.

- `python run.py --browser` opens a normal browser tab instead.
- Set `ANKIJI_PORT` to run a second copy on another port. Never use 8765: that's AnkiConnect.
- After changing `requirements.txt`, delete `.venv\installed.flag` and run `start.bat` again.

Your own `settings.json` (API keys, MyAnimeList link) and `nadeshiko_cache.json` are created in the project folder. They are gitignored: never commit them.

## How the code is laid out

| Path | What it does |
|---|---|
| `run.py` | Starts the server and the app window; closing the window stops the app |
| `app/server.py` | FastAPI routes and settings |
| `app/llm.py` | Card generation through OpenAI, Codex CLI or Claude Code, chunking and cancel |
| `app/extract.py` | Files to text and page images |
| `app/anki.py` | AnkiConnect: decks, duplicate check, adding notes and media |
| `app/media.py` | Example sentences from Nadeshiko |
| `app/nadeshiko.py` | MyAnimeList list and Nadeshiko anime matching (Nadeshiko tab) |
| `app/notetypes.py`, `app/prompts.py` | Ready-made note types and built-in prompts |
| `app/static/index.html` | The whole UI, plain HTML, CSS and JavaScript, no build step |
| `build.bat` | Builds `dist\Ankiji\Ankiji.exe` and `dist\Ankiji.zip` with PyInstaller |

[AGENTS.md](AGENTS.md) has the detailed notes: how each feature works, API quirks and decisions. It's written for AI coding agents but useful for people too.

## Testing

There's no automated test suite yet. Before opening a PR, check the parts you touched by hand:

- the page loads without console errors, in light and dark theme
- generating cards works with at least one AI
- duplicates are marked, and adding to Anki works
- if you touched the build: `build.bat` succeeds and `Ankiji.exe` starts, shows the page and quits when its window closes

Tests are a welcome contribution.

## Building and releases

`build.bat` makes the .exe and the zip locally. On every push to `main`, GitHub Actions runs the same script and replaces the **latest** release with the new `Ankiji.zip`.

Dependabot opens update PRs for the Python packages (weekly, `requirements.txt` and `requirements-build.txt`) and for the GitHub Actions (monthly). Minor and patch updates are merged automatically, which also rebuilds the release. Major updates wait for a person: check out the branch, run `build.bat` and try the app before merging.

## Pull requests

- One topic per PR, on its own branch.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org): `feat: ...`, `fix: ...`, `docs: ...`.
- Keep UI text short and plain, and don't use em dashes in it.
- New dependencies: pin the exact version in `requirements.txt` and mention why in the PR.
- Never commit API keys, `settings.json`, caches or personal data.
