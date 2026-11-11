"""Start Anki Card Maker in its own window.

Runs the local server and opens the page as an app window (Microsoft Edge or Chrome
in app mode: no address bar or tabs). Closing the window stops the app.
If neither browser is found, the normal browser opens instead.

    run.py            app window (default)
    run.py --browser  open in the normal browser and keep running
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

PORT = int(os.environ.get("ANKI_CARD_MAKER_PORT") or 8770)  # never 8765: that is AnkiConnect
URL = f"http://127.0.0.1:{PORT}"


def _fix_streams():
    """A windowed .exe has no console, so stdout/stderr are None and logging would crash."""
    if sys.stdout is None or sys.stderr is None:
        from app.paths import data
        log = open(data("app-log.txt"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _message(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Anki Card Maker", 0x10)
    except Exception:
        print(text)


def _already_running():
    import requests
    try:
        return requests.get(URL + "/api/settings", timeout=1.5).ok
    except requests.RequestException:
        return False


def _find_app_browser():
    pf, pf86, local = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
    candidates = [
        os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("msedge") or shutil.which("chrome")


def _open_window():
    """Open the app window. Returns the browser process, or None if it fell back to the normal browser."""
    from app.paths import data
    exe = _find_app_browser()
    if not exe:
        webbrowser.open(URL)
        return None
    # A profile of its own, so the window is its own process (closing it = app closes)
    # and the user's normal browser is never touched.
    profile = data("browser-profile")
    return subprocess.Popen([
        exe, f"--app={URL}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--window-size=1320,920", "--disable-features=msEdgeStartupBoost,Translate",
    ])


def main():
    _fix_streams()
    use_browser = "--browser" in sys.argv
    if _already_running():  # double-clicked again: just show another window
        if use_browser or not _open_window():
            webbrowser.open(URL)
        return

    import uvicorn
    from app.server import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started or not t.is_alive():
            break
        time.sleep(0.1)
    if not server.started:
        _message(f"Anki Card Maker couldn't start. Something else may be using port {PORT}.\n"
                 "Restart the computer and try again.")
        return

    if use_browser:
        webbrowser.open(URL)
        t.join()
        return
    proc = _open_window()
    if proc is None:  # no Edge or Chrome: the normal browser opened, keep serving
        t.join()
        return
    proc.wait()
    # The window can hand itself to an already-open window of the same profile and
    # exit at once. Keep serving while any window of that profile is still open.
    while _profile_window_open():
        time.sleep(2)
    server.should_exit = True
    t.join(timeout=5)


def _profile_window_open():
    """True if a browser process using our own profile folder is still running."""
    if os.name != "nt":
        return False
    from app.paths import data
    profile = data("browser-profile").lower()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | ForEach-Object { $_.CommandLine }"],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout.lower()
    except Exception:
        return False
    # the main browser process is the one without a --type= (renderer, gpu...) flag
    return any(profile in line and "--type=" not in line for line in out.splitlines())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        _message("Anki Card Maker hit an error:\n\n" + traceback.format_exc()[-1500:])
