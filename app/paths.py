"""Where files live, both when run from source and inside the packaged .exe."""
import os
import sys

FROZEN = getattr(sys, "frozen", False)

# Read-only files that ship with the app (index.html).
BUNDLE_DIR = os.path.join(sys._MEIPASS, "app") if FROZEN else os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BUNDLE_DIR, "static")

# Files the app writes (settings.json, nadeshiko_cache.json, log). Next to the .exe,
# or the project folder when run from source, so everything stays in one place.
DATA_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable(d):
    try:
        probe = os.path.join(d, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


if FROZEN and not _writable(DATA_DIR):  # e.g. unzipped into Program Files
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Ankiji")
    os.makedirs(DATA_DIR, exist_ok=True)


def data(name):
    return os.path.join(DATA_DIR, name)
