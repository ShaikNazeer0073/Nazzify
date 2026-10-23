"""Core (non-GUI) logic for SpotDL Companion. Standard library only."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading

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
AD_TITLES = {"advertisement", "spotify", "spotify free", "spotify premium"}
AUDIO_EXTS = {".m4a", ".mp3", ".webm", ".opus", ".ogg", ".flac", ".wav"}


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


# ---------- locating tools (spotDL / yt-dlp / FFmpeg) ----------
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
    candidates.append(os.path.join(os.path.dirname(sys.executable), "Scripts", "spotdl.exe"))
    found = shutil.which("spotdl")
    if found:
        candidates.append(found)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def find_ffmpeg():
    """Return path to ffmpeg.exe if present at ~/.spotdl/ffmpeg.exe or on PATH."""
    p = os.path.expanduser(os.path.join("~", ".spotdl", "ffmpeg.exe"))
    if os.path.isfile(p):
        return p
    return shutil.which("ffmpeg")


def find_ytdlp(configured_spotdl=""):
    """
    Return command list to execute yt-dlp:
    1. Look for yt-dlp.exe next to spotdl.exe (or configured path)
    2. Look in common Python Scripts locations
    3. Look on PATH
    4. Fallback: [python.exe, "-m", "yt_dlp"] (using python.exe if under pythonw.exe)
    """
    candidates = []
    if configured_spotdl:
        candidates.append(os.path.join(os.path.dirname(configured_spotdl), "yt-dlp.exe"))

    spotdl_exe = find_spotdl(configured_spotdl)
    if spotdl_exe:
        candidates.append(os.path.join(os.path.dirname(spotdl_exe), "yt-dlp.exe"))

    appdata = os.environ.get("APPDATA", "")
    local = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidates.append(os.path.join(appdata, "Python", "Python314", "Scripts", "yt-dlp.exe"))
        candidates += sorted(glob.glob(os.path.join(appdata, "Python", "Python*", "Scripts", "yt-dlp.exe")), reverse=True)
    if local:
        candidates += sorted(glob.glob(os.path.join(local, "Programs", "Python", "Python*", "Scripts", "yt-dlp.exe")), reverse=True)

    candidates.append(os.path.join(os.path.dirname(sys.executable), "Scripts", "yt-dlp.exe"))
    found = shutil.which("yt-dlp")
    if found:
        candidates.append(found)

    for c in candidates:
        if c and os.path.isfile(c):
            return [c]

    # Fallback to python -m yt_dlp
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py_cand = py[:-5] + ".exe"
        if os.path.isfile(py_cand):
            py = py_cand
    elif not os.path.isfile(py):
        py = "python.exe"
    return [py, "-m", "yt_dlp"]


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


# ---------- Titles / Searching / File Naming ----------
def clean_for_search(text):
    """Remove quotes and bracketed extras like (From "DC") or [Telugu] that confuse YouTube search."""
    if not text:
        return ""
    # Iteratively remove nested or multiple pairs of (...), [...], {...}
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", text)
    # Remove straight and curly quotes, backticks
    text = re.sub(r"[\"'\u201c\u201d\u2018\u2019`]", "", text)
    # Remove trailing - From / - Feat / - With
    text = re.sub(r"\s+-\s+(from|feat\.?|with)\b.*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -")


def search_query(title, artist):
    clean_art = clean_for_search(artist)
    clean_tit = clean_for_search(title)
    return f"{clean_art} - {clean_tit}".strip(" -")


def sanitize_filename(name):
    """Remove invalid Windows filename characters: < > : " / \\ | ? * and trailing spaces/dots."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip(". ")
    return name or "track"


def get_track_base(artist, title):
    """Return sanitized artist - title base name."""
    clean_art = clean_for_search(artist)
    clean_tit = clean_for_search(title)
    return sanitize_filename(f"{clean_art} - {clean_tit}")


def song_file_exists(folder, artist, title):
    """Check if an audio file for this artist/title already exists in folder."""
    if not folder or not os.path.isdir(folder):
        return False
    base = get_track_base(artist, title)
    for ext in (".m4a", ".mp3"):
        if os.path.isfile(os.path.join(folder, f"{base}{ext}")):
            return True
    pattern = os.path.join(folder, glob.escape(base) + ".*")
    for p in glob.glob(pattern):
        ext = os.path.splitext(p)[1].lower()
        if ext in AUDIO_EXTS:
            return True
    return False


def build_ytdlp_command(ytdlp_cmd, artist, title, folder, duration=0):
    """
    Build yt-dlp command list:
    - Search with ytsearch5:"<artist> - <title>"
    - Format: bestaudio[ext=m4a]/bestaudio
    - --max-downloads 1, --newline, --ignore-config
    - Duration filter: duration >= X-12 & duration <= X+12 & !is_live
    - FFmpeg location and embed metadata if available
    """
    query = search_query(title, artist)
    base = get_track_base(artist, title)
    # Escape % as %% for yt-dlp output template syntax
    template = os.path.join(folder, f"{base.replace('%', '%%')}.%(ext)s")

    cmd = list(ytdlp_cmd) + [
        "--newline",
        "--ignore-config",
        "--max-downloads", "1",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", template,
    ]

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        cmd += [
            "--ffmpeg-location", ffmpeg,
            "--embed-metadata",
            "--embed-thumbnail",
            "--convert-thumbnails", "jpg",
        ]

    dur = int(duration or 0)
    if dur > 0:
        min_d = max(0, dur - 12)
        max_d = dur + 12
        cmd += ["--match-filters", f"duration >= {min_d} & duration <= {max_d} & !is_live"]
    else:
        cmd += ["--match-filters", "!is_live"]

    cmd.append(f"ytsearch5:{query}")
    return cmd, base


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
    if any(k in t for k in ("confirm you're not a bot", "sign in to confirm", "bot", "sign in")):
        return ("YouTube asked for sign-in/bot confirmation. "
                "Try updating yt-dlp in Command Prompt: pip install -U yt-dlp")
    if "ffmpeg" in t and ("not found" in t or "not installed" in t or "no such file" in t):
        return ("FFmpeg was not found. Install FFmpeg or run: "
                "\"%APPDATA%\\Python\\Python314\\Scripts\\spotdl.exe\" --download-ffmpeg")
    if any(k in t for k in ("connectionerror", "getaddrinfo", "timed out", "max retries", "network is unreachable")):
        return "Network problem. Check your internet connection and try again."
    if "does not pass filter" in t and "finished downloading playlist" in t:
        return "No YouTube recording matched the Spotify track duration within +/- 12s."
    if "ratelimit" in t or "429" in t:
        return "YouTube is rate-limiting requests. Wait a few minutes and retry."
    return f"Download ended with exit code {returncode}. See log for details."


def run_download(cmd, on_line, holder=None):
    """Run yt-dlp subprocess, streaming cleaned lines to on_line. Returns (returncode, full_text)."""
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
    """Returns dict: ok(bool), title, artist, status, duration, message."""
    if os.name != "nt":
        return {"ok": False, "message": "Song detection only works on Windows."}
    if not os.path.isfile(PS_SCRIPT):
        return {"ok": False, "message": "Helper file get_media_info.ps1 is missing."}
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
        return {"ok": False, "message": "Spotify is not reporting a media session. Start playing a song in Spotify."}
    def is_valid(d):
        t = (d.get("title") or "").strip()
        a = (d.get("artist") or "").strip()
        return bool(t and a and t.lower() not in AD_TITLES)

    # Sort to prioritize valid songs first (playing before paused), ads last
    spot.sort(key=lambda d: (
        0 if (is_valid(d) and str(d.get("status", "")).lower() == "playing")
        else (1 if is_valid(d) else 2)
    ))
    d = spot[0]
    title = (d.get("title") or "").strip()
    artist = (d.get("artist") or "").strip()
    status = str(d.get("status", "")).strip()
    duration = int(d.get("duration", 0) or 0)
    if not is_valid(d):
        return {"ok": False, "message": "Spotify is showing an advertisement or no track info. Wait for the song and retry."}
    return {"ok": True, "title": title, "artist": artist, "status": status, "duration": duration, "message": ""}


def start_media_listener(on_song, on_error=None, parent_pid=None):
    """
    Run get_media_info.ps1 with -Loop -ParentPid in a persistent subprocess.
    Calls on_song(dict) for each JSON line emitted.
    Returns (proc, thread).
    """
    if os.name != "nt" or not os.path.isfile(PS_SCRIPT):
        return None, None
    ps = shutil.which("powershell.exe") or "powershell.exe"
    pid = str(parent_pid if parent_pid is not None else os.getpid())
    cmd = [
        ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", PS_SCRIPT, "-Loop", "-ParentPid", pid,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    def reader():
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    res = interpret_media_output(line)
                    on_song(res)
        except Exception as e:
            if on_error:
                on_error(e)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return proc, t
