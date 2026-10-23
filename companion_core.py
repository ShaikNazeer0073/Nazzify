"""Core (non-GUI) logic for SpotDL Companion. Standard library only."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

APP_NAME = "SpotDLCompanion"
HERE = os.path.dirname(os.path.abspath(__file__))
PS_SCRIPT = os.path.join(HERE, "get_media_info.ps1")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
PCT_RE = re.compile(r"(\d{1,3})(?:\.\d+)?\s*%")
URL_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist|artist)/[A-Za-z0-9]+",
    re.IGNORECASE,
)
AD_TITLES = {"advertisement", "spotify", "spotify free"}


# ---------- configuration ----------
def config_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


def default_music_folder():
    return os.path.join(os.path.expanduser("~"), "Music", "SpotDL Companion")


def load_config():
    cfg = {"download_folder": default_music_folder(), "spotdl_path": ""}
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


# ---------- locating spotDL ----------
def find_spotdl(configured=""):
    """Return path to spotdl executable or None."""
    candidates = []
    if configured:
        candidates.append(configured)
    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidates.append(os.path.join(appdata, "Python", "Python314", "Scripts", "spotdl.exe"))
        candidates += sorted(glob.glob(os.path.join(appdata, "Python", "Python*", "Scripts", "spotdl.exe")), reverse=True)
    if local:
        candidates += sorted(glob.glob(os.path.join(local, "Programs", "Python", "Python*", "Scripts", "spotdl.exe")), reverse=True)
    # Scripts folder next to the Python running this app
    candidates.append(os.path.join(os.path.dirname(sys.executable), "Scripts", "spotdl.exe"))
    found = shutil.which("spotdl")
    if found:
        candidates.append(found)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def spotdl_version(exe):
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW, env=child_env())
        out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
        return out.splitlines()[-1] if out else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NO_COLOR"] = "1"
    env.setdefault("COLUMNS", "200")
    return env


# ---------- URLs / commands ----------
def classify_url(text):
    """Return 'track'/'album'/'playlist'/'artist' or None."""
    m = URL_RE.match((text or "").strip())
    return m.group(1).lower() if m else None


def clean_url(text):
    return (text or "").strip().split("?")[0]


def build_command(exe, query, folder, kind="track", fmt=None):
    """Argument list (no shell). kind: track/search -> flat files; album/playlist -> subfolder."""
    if kind in ("album", "playlist", "artist"):
        template = os.path.join(folder, "{list-name}", "{artists} - {title}.{output-ext}")
    else:
        template = os.path.join(folder, "{artists} - {title}.{output-ext}")
    cmd = [exe, "download", query, "--output", template]
    if fmt == "m4a":      # YouTube Music already serves m4a: no slow re-encoding
        cmd += ["--format", "m4a", "--bitrate", "disable"]
    elif fmt == "mp3":
        cmd += ["--format", "mp3"]
    return cmd


def looks_like_arg_error(text):
    t = text.lower()
    return ("unrecognized arguments" in t or "invalid choice" in t or "expected one of" in t
            or "error: argument" in t or "usage: spotdl" in t)


def clean_for_search(text):
    """Remove quotes and bracketed extras like (From "DC"), [Remastered] that confuse YouTube Music search."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    text = re.sub(r"[\"\u201c\u201d\u2018\u2019`]", "", text)
    text = re.sub(r"\s+-\s+(from|feat\.?|with)\b.*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -")


def search_query(title, artist):
    return f"{clean_for_search(artist)} - {clean_for_search(title)}".strip(" -")


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def parse_progress(line):
    m = PCT_RE.search(line)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    return None


def friendly_error(output_text, returncode):
    t = output_text.lower()
    if "ffmpeg" in t and ("not found" in t or "not installed" in t or "no such file" in t):
        return ("spotDL could not find FFmpeg. Run this once in Command Prompt:  "
                "\"%APPDATA%\\Python\\Python314\\Scripts\\spotdl.exe\" --download-ffmpeg")
    if any(k in t for k in ("connectionerror", "getaddrinfo", "timed out", "max retries", "network is unreachable")):
        return "Network problem. Check your internet connection and try again."
    if "lookuperror" in t or "no results found" in t or "no usable results" in t:
        return ("spotDL could not find a matching recording. Try pasting the song's Spotify link "
                "(Spotify: right-click song > Share > Copy Song Link) into box 2.")
    if "ratelimit" in t or "429" in t:
        return "Spotify/YouTube is rate-limiting requests. Wait a few minutes and retry."
    if "invalid" in t and "url" in t:
        return "spotDL said the link is not valid."
    return f"spotDL finished with an error (exit code {returncode}). See the log for details."


def run_download(cmd, on_line, holder=None):
    """Run spotDL, streaming cleaned lines to on_line. Returns (returncode, full_text)."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW, env=child_env(), shell=False,
    )
    if holder is not None:
        holder["proc"] = proc
    chunks = []
    buf = b""
    while True:
        b = proc.stdout.read(1)
        if not b:
            break
        if b in (b"\n", b"\r"):
            if buf:
                line = strip_ansi(buf.decode("utf-8", "replace")).strip()
                buf = b""
                if line:
                    chunks.append(line)
                    on_line(line)
        else:
            buf += b
    if buf:
        line = strip_ansi(buf.decode("utf-8", "replace")).strip()
        if line:
            chunks.append(line)
            on_line(line)
    proc.wait()
    return proc.returncode, "\n".join(chunks)


# ---------- current song (Windows GSMTC via PowerShell) ----------
def get_current_song():
    """Returns dict: ok(bool), title, artist, status, message."""
    if os.name != "nt":
        return {"ok": False, "message": "Song detection only works on Windows."}
    if not os.path.isfile(PS_SCRIPT):
        return {"ok": False, "message": "Helper file get_media_info.ps1 is missing. Re-extract the ZIP."}
    ps = shutil.which("powershell.exe") or "powershell.exe"
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", PS_SCRIPT],
            capture_output=True, timeout=25, creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "Windows did not answer in time. Try again."}
    except OSError as e:
        return {"ok": False, "message": f"Could not start Windows PowerShell: {e}"}
    return interpret_media_output(r.stdout.decode("utf-8-sig", "replace"), r.stderr.decode("utf-8", "replace"))


def interpret_media_output(stdout, stderr=""):
    stdout = stdout.strip()
    if not stdout:
        return {"ok": False, "message": "Could not read Windows media info. " + (stderr.strip()[:200] or "")}
    try:
        data = json.loads(stdout)
    except ValueError:
        return {"ok": False, "message": "Unexpected reply from media helper: " + stdout[:200]}
    if isinstance(data, dict):
        data = [data]
    if not data:
        return {"ok": False, "message": "No media session found. Open Spotify and play a song first."}
    spot = [d for d in data if "spotify" in str(d.get("app", "")).lower()]
    if not spot:
        return {"ok": False, "message": "Spotify is not reporting a media session. Start playing a song in Spotify "
                                        "(other apps' media is ignored on purpose)."}
    spot.sort(key=lambda d: 0 if str(d.get("status", "")).lower() == "playing" else 1)
    d = spot[0]
    title = (d.get("title") or "").strip()
    artist = (d.get("artist") or "").strip()
    status = str(d.get("status", "")).strip()
    if not title or title.lower() in AD_TITLES or not artist:
        return {"ok": False, "message": "Spotify is showing an advertisement or no track info. Wait for the song and retry."}
    return {"ok": True, "title": title, "artist": artist, "status": status, "message": ""}
