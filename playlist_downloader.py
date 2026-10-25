"""
Smart playlist / album / track download engine for SpotDL Companion.

Architecture
============
Phase 1 – Metadata:
    `spotdl save <url> --save-file <tmp>.spotdl` extracts the rich Spotify
    metadata (title, artists, album_name, duration, isrc, cover_url, song_id)
    without downloading audio.

Phase 2 – Concurrent Download (3 workers):
    Each pending track runs yt-dlp with an intelligent fallback cascade:
      1. ytmsearch5:<artist> - <title>           (YouTube Music primary)
      2. ytsearch5:<artist> - <title> audio      (YouTube + audio keyword)
      3. ytsearch3:<title> <artist>              (Reversed order)
      4. ytsearch3:<title> <album>               (Title + album)
      5. ytsearch3:<title>                       (Title-only fallback)

    Candidate Evaluation & Anti-Substitution:
      - Duration tolerance filter: ±15 seconds & !is_live.
      - Disallows version substitution: live, cover, remix, instrumental,
        karaoke, acoustic, lo-fi, slowed, sped up, or language switches
        (unless the original title specifically contains them).
      - If candidate is questionable, flags as "review" instead of blind failure.

Phase 3 – Spotify Cover Embedding:
    After audio is downloaded, the exact Spotify cover image from cover_url
    is downloaded and embedded into the audio tags (MP3 ID3 / M4A MP4).

State & Resume:
    Saved in <folder>/.nazzify/<hash>.json.
    Tracks have status: 'pending' | 'done' | 'review' | 'failed'.
    Re-running an interrupted download skips completed tracks.
    Supports retry_failed() to re-attempt tracks that failed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import companion_core as core

# ── Tunables ────────────────────────────────────────────────────────────────────
WORKERS = 3             # 3 concurrent yt-dlp workers
TRACK_TIMEOUT = 120     # Seconds budget per track attempt
MIN_BYTES = 80_000      # 80 KB minimum valid audio file size
DUR_TOL = 15            # ±seconds duration tolerance
STATE_DIR = ".nazzify"

_SUBSTITUTE_RE = re.compile(
    r"\b(live(?: at| version)?|cover(?: version)?|remix|instrumental|"
    r"karaoke|tribute|acoustic|orchestral?|lo.?fi|slowed|reverb|sped up|"
    r"remaster(?:ed)?|flute|ringtone|promo)\b",
    re.IGNORECASE,
)

_LANG_RE = re.compile(
    r"\b(telugu|tamil|hindi|kannada|malayalam|bengali|punjabi|marathi)\b",
    re.IGNORECASE,
)


def _child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["NO_COLOR"] = "1"
    env.setdefault("COLUMNS", "200")
    return env


# ══════════════════════════════════════════════════════════════════════════════
# State management (JSON persistence)
# ══════════════════════════════════════════════════════════════════════════════

def _state_path(folder: str, url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    d = os.path.join(folder, STATE_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.json")


def load_state(folder: str, url: str) -> dict:
    p = _state_path(folder, url)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"url": url, "name": "", "total": 0, "tracks": {}}


def _save_state(folder: str, url: str, state: dict, lock: threading.Lock):
    with lock:
        p = _state_path(folder, url)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1: Metadata extraction
# ══════════════════════════════════════════════════════════════════════════════

def fetch_spotify_embed_metadata(url: str) -> list[dict]:
    """
    Direct, instant extraction of Spotify track/album/playlist metadata via
    Spotify's embed API (__NEXT_DATA__).
    Executes in <1 second without external CLI dependencies.
    """
    m = re.search(r"(track|album|playlist)/([A-Za-z0-9]+)", url)
    if not m:
        return []
    kind, sid = m.group(1).lower(), m.group(2)
    embed_url = f"https://open.spotify.com/embed/{kind}/{sid}"
    try:
        req = urllib.request.Request(embed_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
        sm = re.search(r'<script id=.__NEXT_DATA__.[^>]*>(.*?)</script>', html)
        if not sm:
            return []
        data = json.loads(sm.group(1))
        entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
        if not entity:
            return []

        images = entity.get("visualIdentity", {}).get("image", [])
        cover_url = ""
        if images and isinstance(images, list):
            sorted_imgs = sorted(images, key=lambda x: x.get("maxHeight", 0), reverse=True)
            cover_url = sorted_imgs[0].get("url", "")

        coll_name = entity.get("title") or entity.get("name") or "Spotify"

        tracks = []
        if kind == "track":
            raw_artists = entity.get("artists") or []
            if isinstance(raw_artists, list):
                art_names = [a.get("name", "") for a in raw_artists if isinstance(a, dict) and a.get("name")]
            else:
                art_names = [str(raw_artists)]
            if not art_names and entity.get("subtitle"):
                art_names = [a.strip() for a in re.split(r"[,/]+", entity["subtitle"]) if a.strip()]

            dur_ms = entity.get("duration") or 0
            tracks.append({
                "name": entity.get("title") or entity.get("name") or "",
                "artists": art_names,
                "album_name": entity.get("album", {}).get("name") if isinstance(entity.get("album"), dict) else coll_name,
                "duration": int(dur_ms) // 1000 if dur_ms else 0,
                "cover_url": cover_url,
                "song_id": sid,
                "isrc": "",
                "list_name": coll_name,
            })
        else:
            raw_track_list = entity.get("trackList", [])
            for t in raw_track_list:
                t_title = t.get("title") or t.get("name") or ""
                sub = t.get("subtitle") or ""
                art_names = [a.strip() for a in re.split(r"[,/\u00a0]+", sub) if a.strip()]
                dur_ms = t.get("duration") or 0
                tid = t.get("uri", "").split(":")[-1] or t.get("uid", "")
                tracks.append({
                    "name": t_title,
                    "artists": art_names,
                    "album_name": coll_name if kind == "album" else "",
                    "duration": int(dur_ms) // 1000 if dur_ms else 0,
                    "cover_url": cover_url,
                    "song_id": tid,
                    "isrc": "",
                    "list_name": coll_name,
                })
        return tracks
    except Exception:
        return []


def fetch_collection(spotdl_exe: str, url: str) -> dict | None:
    """
    Fetch complete Spotify collection metadata (track, album, or playlist) WITHOUT downloading audio.
    Returns dict:
      kind: 'track' | 'album' | 'playlist'
      name: collection title
      owner: artist or playlist owner name
      cover_url: high-res cover image URL
      tracks: list of track dicts with name, artists, album_name, duration, cover_url, song_id, isrc
      total: number of tracks
    """
    m = re.search(r"(track|album|playlist)/([A-Za-z0-9]+)", url)
    if not m:
        return None
    kind, sid = m.group(1).lower(), m.group(2)
    embed_url = f"https://open.spotify.com/embed/{kind}/{sid}"
    try:
        req = urllib.request.Request(embed_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
        sm = re.search(r'<script id=.__NEXT_DATA__.[^>]*>(.*?)</script>', html)
        if sm:
            data = json.loads(sm.group(1))
            entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
            if entity:
                images = entity.get("visualIdentity", {}).get("image", [])
                cover_url = ""
                if images and isinstance(images, list):
                    sorted_imgs = sorted(images, key=lambda x: x.get("maxHeight", 0), reverse=True)
                    cover_url = sorted_imgs[0].get("url", "")

                coll_name = entity.get("title") or entity.get("name") or "Spotify"
                owner = entity.get("subtitle") or ""

                tracks = []
                if kind == "track":
                    raw_artists = entity.get("artists") or []
                    if isinstance(raw_artists, list):
                        art_names = [a.get("name", "") for a in raw_artists if isinstance(a, dict) and a.get("name")]
                    else:
                        art_names = [str(raw_artists)]
                    if not owner:
                        owner = ", ".join(art_names)
                    dur_ms = entity.get("duration") or 0
                    tracks.append({
                        "name": coll_name,
                        "artists": art_names,
                        "album_name": entity.get("album", {}).get("name") if isinstance(entity.get("album"), dict) else coll_name,
                        "duration": int(dur_ms) // 1000 if dur_ms else 0,
                        "cover_url": cover_url,
                        "song_id": sid,
                        "isrc": "",
                        "list_name": coll_name,
                    })
                else:
                    raw_track_list = entity.get("trackList", [])
                    for t in raw_track_list:
                        t_title = t.get("title") or t.get("name") or ""
                        sub = t.get("subtitle") or ""
                        art_names = [a.strip() for a in re.split(r"[,/\u00a0]+", sub) if a.strip()]
                        dur_ms = t.get("duration") or 0
                        tid = t.get("uri", "").split(":")[-1] or t.get("uid", "")
                        tracks.append({
                            "name": t_title,
                            "artists": art_names,
                            "album_name": coll_name if kind == "album" else "",
                            "duration": int(dur_ms) // 1000 if dur_ms else 0,
                            "cover_url": cover_url,
                            "song_id": tid,
                            "isrc": "",
                            "list_name": coll_name,
                        })

                return {
                    "kind": kind,
                    "name": coll_name,
                    "owner": owner,
                    "cover_url": cover_url,
                    "tracks": tracks,
                    "total": len(tracks),
                }
    except Exception:
        pass

    # Fallback to spotdl save
    tracks = fetch_metadata(spotdl_exe, url)
    if not tracks:
        return None
    t0 = tracks[0]
    coll_name = t0.get("album_name") or t0.get("list_name") or kind.capitalize()
    artists = t0.get("artists") or []
    owner = ", ".join(artists) if isinstance(artists, list) else str(artists)
    cover_url = t0.get("cover_url") or ""
    return {
        "kind": kind,
        "name": coll_name,
        "owner": owner,
        "cover_url": cover_url,
        "tracks": tracks,
        "total": len(tracks),
    }


def fetch_metadata(spotdl_exe: str, url: str) -> list[dict]:
    """
    Fetch Spotify track metadata.
    1. Direct instant extraction from Spotify embed API (<1s, ultra-reliable).
    2. Fallback to `spotdl save` if embed scraping fails.
    """
    # 1. Primary: Instant direct Spotify embed fetch
    tracks = fetch_spotify_embed_metadata(url)
    if tracks:
        return tracks

    # 2. Fallback: spotdl save with clean URL (strip ?si=...) and 45s timeout
    if not spotdl_exe or not os.path.isfile(spotdl_exe):
        return []
    m = re.search(r"(track|album|playlist)/([A-Za-z0-9]+)", url)
    clean_url = f"https://open.spotify.com/{m.group(1)}/{m.group(2)}" if m else url
    tmp = tempfile.mktemp(suffix=".spotdl")
    try:
        cmd = [spotdl_exe, "save", clean_url, "--save-file", tmp, "--threads", "4"]
        subprocess.run(
            cmd,
            capture_output=True,
            timeout=45,
            creationflags=core.CREATE_NO_WINDOW,
            env=_child_env(),
        )
        if not os.path.isfile(tmp):
            return []
        with open(tmp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3: Cover Art Retrieval
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_cover(cover_url: str, track_id: str) -> str:
    """Download Spotify cover to temp cache. Returns local file path or ''."""
    if not cover_url:
        return ""
    art_dir = os.path.join(tempfile.gettempdir(), "SpotDLCompanionArt")
    os.makedirs(art_dir, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", track_id)[:40]
    dest = os.path.join(art_dir, f"lnk_{safe_id}.jpg")
    if os.path.isfile(dest) and os.path.getsize(dest) > 2_000:
        return dest
    try:
        req = urllib.request.Request(cover_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Anti-Substitution & Filtering
# ══════════════════════════════════════════════════════════════════════════════

def _is_substitute(candidate: str, original: str) -> bool:
    """True if candidate contains remix/live/cover/language tags missing in original."""
    c_words = {m.group(1).lower() for m in _SUBSTITUTE_RE.finditer(candidate)}
    o_words = {m.group(1).lower() for m in _SUBSTITUTE_RE.finditer(original)}
    diff = c_words - o_words
    if diff:
        return True

    # Language tag check: reject if candidate has a specific language that original lacks
    c_langs = {m.group(1).lower() for m in _LANG_RE.finditer(candidate)}
    o_langs = {m.group(1).lower() for m in _LANG_RE.finditer(original)}
    if c_langs - o_langs:
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# Multi-artist File Finder
# ══════════════════════════════════════════════════════════════════════════════

def _find_output(folder: str, artists: list[str], title: str) -> str:
    """Locate downloaded audio file considering various artist naming patterns."""
    if not os.path.isdir(folder):
        return ""

    candidates = []
    if artists:
        candidates.append(", ".join(artists))
        candidates.append(artists[0])
        candidates.append(" & ".join(artists))
    candidates.append("")

    for art in candidates:
        p = core.find_audio_file(folder, art, title) if art else None
        if p and os.path.isfile(p) and os.path.getsize(p) >= MIN_BYTES:
            return p

    # Fallback substring search across directory
    clean = core.sanitize_filename(core.clean_for_search(title)).lower()
    if clean:
        try:
            for fn in os.listdir(folder):
                ext = os.path.splitext(fn)[1].lower()
                if ext in core.AUDIO_EXTS and clean in fn.lower():
                    fp = os.path.join(folder, fn)
                    if os.path.getsize(fp) >= MIN_BYTES:
                        return fp
        except OSError:
            pass
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2: Single-Track Downloader with Cascaded Fallback
# ══════════════════════════════════════════════════════════════════════════════

def _download_track(
    ytdlp_cmd: list,
    track_data: dict,
    folder: str,
    stop_event: threading.Event,
    on_log,
) -> tuple[str, str]:
    """
    Download one track using cascaded queries and strict candidate evaluation.
    Returns (status, output_path) where status is 'done' | 'review' | 'failed'.
    """
    name = (track_data.get("name") or "").strip()
    raw_artists = track_data.get("artists") or []
    artists = raw_artists if isinstance(raw_artists, list) else [str(raw_artists)]
    primary_artist = artists[0] if artists else ""
    artist_pair = ", ".join(artists[:2]) if len(artists) > 1 else primary_artist
    duration = int(track_data.get("duration") or 0)
    album_name = (track_data.get("album_name") or "").strip()

    clean_art = core.clean_for_search(primary_artist)
    clean_pair = core.clean_for_search(artist_pair)
    clean_tit = core.clean_for_search(name)
    clean_alb = core.clean_for_search(album_name)

    base = core.sanitize_filename(f"{clean_pair} - {clean_tit}")
    template = os.path.join(folder, f"{base.replace('%', '%%')}.%(ext)s")

    ffmpeg = core.find_ffmpeg()
    base_cmd = list(ytdlp_cmd) + [
        "--newline",
        "--ignore-config",
        "--no-playlist",
        "--max-downloads", "1",
        "--concurrent-fragments", "4",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", template,
    ]
    if ffmpeg:
        base_cmd += ["--ffmpeg-location", ffmpeg, "--embed-metadata"]

    # Duration filter to avoid preview snippets or full movie clips
    if duration > 0:
        lo = max(0, duration - DUR_TOL)
        hi = duration + DUR_TOL
        dur_filter = f"duration >= {lo} & duration <= {hi} & !is_live"
    else:
        dur_filter = "!is_live"
    base_cmd += ["--match-filters", dur_filter]

    # Prioritised search cascade
    queries = [
        f"ytmsearch5:{clean_art} - {clean_tit}",
        f"ytsearch5:{clean_art} - {clean_tit} audio",
        f"ytsearch3:{clean_tit} {clean_art}",
    ]
    if clean_alb and clean_alb.lower() != clean_tit.lower():
        queries.append(f"ytsearch3:{clean_tit} {clean_alb}")
    queries.append(f"ytsearch3:{clean_tit}")

    last_err = ""
    for q_idx, q in enumerate(queries):
        if stop_event.is_set():
            return "failed", ""

        existing = _find_output(folder, artists, name)
        if existing:
            return "done", existing

        cmd = base_cmd + [q]
        labels = ["YT Music", "YouTube (Audio)", "YouTube (Reversed)", "YouTube (Album)", "YouTube (Title)"]
        slabel = labels[q_idx] if q_idx < len(labels) else "YouTube"
        on_log(f"  [{slabel}] Searching: {clean_art} – {clean_tit}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=core.CREATE_NO_WINDOW,
                env=_child_env(),
            )
            lines: list[str] = []
            buf = b""
            deadline = time.time() + TRACK_TIMEOUT

            while not stop_event.is_set() and time.time() < deadline:
                b = proc.stdout.read(1)
                if not b:
                    break
                if b in (b"\n", b"\r"):
                    if buf:
                        l = core.strip_ansi(buf.decode("utf-8", "replace")).strip()
                        buf = b""
                        if l:
                            lines.append(l)
                else:
                    buf += b

            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                last_err = "timeout"
                continue

            text = "\n".join(lines)
            out = _find_output(folder, artists, name)
            if out:
                # Anti-substitution verification from downloaded video title
                m_tit = re.search(r"(?:Destination|title).*?([^\\/\n]+)\.\w{2,5}", text)
                cand_title = m_tit.group(1) if m_tit else ""
                if cand_title and _is_substitute(cand_title, name):
                    on_log(f"  ⚠ Flagged for review (potential version substitute '{cand_title}'): {name}")
                    return "review", out
                return "done", out

            if "does not pass filter" in text or "does not pass --match-filters" in text:
                last_err = "duration_mismatch"
                # On the very last fallback attempt, try relaxed filter
                if q_idx == len(queries) - 1:
                    relaxed_cmd = [x for x in cmd if "duration" not in x and "!is_live" not in x]
                    relaxed_cmd = [x for x in relaxed_cmd if x != "--match-filters"]
                    try:
                        subprocess.run(
                            relaxed_cmd,
                            capture_output=True,
                            timeout=TRACK_TIMEOUT,
                            creationflags=core.CREATE_NO_WINDOW,
                            env=_child_env(),
                        )
                        out2 = _find_output(folder, artists, name)
                        if out2:
                            on_log(f"  ⚠ Downloaded with relaxed duration filter (verify manually): {name}")
                            return "review", out2
                    except Exception:
                        pass
                continue

            last_err = f"rc={proc.returncode}"

        except Exception as exc:
            last_err = str(exc)[:80]
            continue

    on_log(f"  ✗ Not found after all sources: {primary_artist} – {name} ({last_err})")
    return "failed", ""


def create_collection_zip(dest_dir: str, collection_name: str, file_paths: list[str]) -> str | None:
    """
    Create a ZIP archive of finished audio files using ZIP_STORED and allowZip64=True.
    Writes to .zip.part temporary file and atomically renames.
    Handles existing files by appending (2), (3), etc.
    """
    import zipfile
    safe_name = core.sanitize_filename(collection_name) or "Collection"
    base_zip = os.path.join(dest_dir, f"{safe_name}.zip")
    final_zip = base_zip
    counter = 2
    while os.path.exists(final_zip):
        final_zip = os.path.join(dest_dir, f"{safe_name} ({counter}).zip")
        counter += 1

    part_zip = final_zip + ".part"
    try:
        with zipfile.ZipFile(part_zip, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for fp in file_paths:
                if fp and os.path.isfile(fp) and os.path.getsize(fp) >= MIN_BYTES:
                    if STATE_DIR in fp:
                        continue
                    arcname = os.path.basename(fp)
                    zf.write(fp, arcname=arcname)
        if os.path.exists(part_zip) and os.path.getsize(part_zip) > 0:
            os.replace(part_zip, final_zip)
            return final_zip
        else:
            if os.path.exists(part_zip):
                try:
                    os.remove(part_zip)
                except OSError:
                    pass
            return None
    except Exception:
        if os.path.exists(part_zip):
            try:
                os.remove(part_zip)
            except OSError:
                pass
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Public PlaylistDownloader Engine
# ══════════════════════════════════════════════════════════════════════════════

class PlaylistDownloader:
    """
    Concurrent, resume-capable engine for Spotify tracks, albums, and playlists.
    """

    def __init__(self, ytdlp_cmd: list, spotdl_exe: str, gui_queue):
        self.ytdlp_cmd = ytdlp_cmd
        self.spotdl_exe = spotdl_exe
        self.q = gui_queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()

    def fetch_collection(self, url: str) -> dict | None:
        """Fetch metadata without downloading audio."""
        return fetch_collection(self.spotdl_exe, url)

    def start(self, url: str, folder: str, selected_track_ids: list[str] | set[str] | None = None, options: dict | None = None):
        """Begin or resume download asynchronously for selected tracks."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(url, folder, selected_track_ids, options or {}),
            daemon=True,
            name="PlaylistDL",
        )
        self._thread.start()

    def stop(self):
        """Signal all workers to stop cleanly."""
        self._stop.set()

    def _emit(self, kind: str, val):
        self.q.put((kind, val))

    def _log(self, msg: str):
        self._emit("link_line", msg)

    def _run(self, url: str, folder: str, selected_track_ids=None, options=None):
        options = options or {}
        stop = self._stop
        kind, _ = core.parse_spotify_url(url)
        kind_label = {"track": "Track", "album": "Album", "playlist": "Playlist"}.get(kind or "", "Link")

        # ── Phase 1: Fetch Spotify Metadata ──────────────────────────────────
        self._emit("link_progress", ("fetching", 0, 0, f"Fetching {kind_label} metadata from Spotify…"))
        coll_info = fetch_collection(self.spotdl_exe, url)

        if not coll_info or not coll_info.get("tracks"):
            self._emit("link_progress", ("error", 0, 0,
                "Could not fetch track list from Spotify. Please check connection and link."))
            self._emit("link_job_done", (url, kind or "link", 1, "metadata_fetch_failed", folder, None))
            return

        tracks_raw = coll_info["tracks"]
        coll_name = coll_info.get("name") or kind_label

        # Subfolder option: Save in a folder named after the album/playlist (default True)
        effective_folder = folder
        if options.get("subfolder", True) and kind in ("album", "playlist"):
            sub_name = core.sanitize_filename(coll_name)
            effective_folder = os.path.join(folder, sub_name)
            os.makedirs(effective_folder, exist_ok=True)

        # ── Phase 2: Build / Merge Persistent State ──────────────────────────
        state = load_state(effective_folder, url)
        state["url"] = url
        state["name"] = state.get("name") or coll_name

        id_to_data: dict[str, dict] = {}
        for td in tracks_raw:
            tid = td.get("song_id") or td.get("name", "unknown")
            id_to_data[tid] = td

        sel_set = set(selected_track_ids) if selected_track_ids is not None else set(id_to_data.keys())

        # IMPORTANT resume/state fix:
        # unselected tracks must NOT be saved as "pending" in .nazzify state.
        for tid, td in id_to_data.items():
            is_selected = tid in sel_set
            if tid not in state["tracks"]:
                state["tracks"][tid] = {
                    "title": td.get("name", ""),
                    "artists": td.get("artists", []),
                    "duration": td.get("duration", 0),
                    "isrc": td.get("isrc", ""),
                    "cover_url": td.get("cover_url", ""),
                    "status": "pending" if is_selected else "unselected",
                    "output_file": "",
                    "error": "",
                    "attempts": 0,
                }
            else:
                ts = state["tracks"][tid]
                if td.get("cover_url"):
                    ts["cover_url"] = td["cover_url"]
                if is_selected:
                    if ts.get("status") in ("unselected", "failed"):
                        ts["status"] = "pending"
                        ts["attempts"] = 0
                        ts["error"] = ""
                else:
                    if ts.get("status") == "pending":
                        ts["status"] = "unselected"

        state["total"] = len(id_to_data)
        _save_state(effective_folder, url, state, self._state_lock)

        # Build list of pending items from selected tracks
        pending = []
        for tid in sel_set:
            if stop.is_set():
                break
            ts = state["tracks"].get(tid)
            if not ts:
                continue
            td = id_to_data.get(tid)
            if not td:
                continue

            if ts["status"] == "pending":
                pending.append((tid, ts, td))
            elif ts["status"] in ("done", "review"):
                out = ts.get("output_file", "")
                if not out or not os.path.isfile(out) or os.path.getsize(out) < MIN_BYTES:
                    ts["status"] = "pending"
                    ts["attempts"] = 0
                    pending.append((tid, ts, td))

        total_selected = len(sel_set)
        if not pending:
            done_n = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") in ("done", "review"))
            self._emit("link_progress", ("finished", done_n, total_selected, f"All {total_selected} selected songs already downloaded ✓"))
            self._emit("link_job_done", (url, kind or "link", 0, "all_already_done", effective_folder, None))
            return

        self._log(f"{len(pending)} song(s) to download ({total_selected - len(pending)} already downloaded)")
        self._emit("link_progress", ("started", total_selected - len(pending), total_selected, f"Downloading {len(pending)} of {total_selected} songs…"))

        # ── Phase 3: Concurrent Worker Pool (3 workers) ──────────────────────
        done_count = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") in ("done", "review"))
        fail_count = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") == "failed")

        def process_one(item: tuple):
            nonlocal done_count, fail_count
            if stop.is_set():
                return
            tid, ts, td = item
            name = ts["title"]
            artists = ts.get("artists") or []
            artist_str = ", ".join(artists) if isinstance(artists, list) else str(artists)

            ts["attempts"] = ts.get("attempts", 0) + 1
            self._log(f"→ Starting: {artist_str} – {name}")
            self._emit("link_track_status", (tid, "Downloading"))

            status, out_path = _download_track(
                self.ytdlp_cmd, td, effective_folder, stop, self._log
            )

            # Embed high-resolution Spotify cover
            if status in ("done", "review") and out_path:
                cover_url = ts.get("cover_url") or td.get("cover_url", "")
                if cover_url:
                    cover_file = _fetch_cover(cover_url, tid)
                    if cover_file:
                        try:
                            core.embed_spotify_cover(out_path, cover_file)
                        except Exception:
                            pass

            with self._state_lock:
                ts["status"] = status
                ts["output_file"] = out_path
                ts["error"] = "" if status in ("done", "review") else "Not found after all sources"

                if status in ("done", "review"):
                    done_count += 1
                    marker = "✓" if status == "done" else "⚠"
                    label = f"{marker} {done_count}/{total_selected} — {name}"
                    if status == "review":
                        label += " (needs review)"
                    self._emit("link_track_status", (tid, "✓ Done" if status == "done" else "⚠ Needs review"))
                else:
                    fail_count += 1
                    label = f"✗ Failed ({fail_count}/{len(pending)}) — {name}"
                    self._emit("link_track_status", (tid, "✗ Not found"))

            _save_state(effective_folder, url, state, self._state_lock)
            self._emit("link_progress", (status, done_count, total_selected, label))

        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="PDL") as pool:
            list(pool.map(process_one, pending))

        # ── ZIP and Summary ──────────────────────────────────────────────────
        finished_files = []
        with self._state_lock:
            final_done = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") in ("done", "review"))
            final_fail = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") == "failed")
            final_rev  = sum(1 for tid in sel_set if state["tracks"].get(tid, {}).get("status") == "review")
            for tid in sel_set:
                t = state["tracks"].get(tid)
                if t and t["status"] in ("done", "review") and t.get("output_file"):
                    finished_files.append(t["output_file"])

        created_zip = None
        if options.get("create_zip") and not stop.is_set() and finished_files:
            self._log("Creating ZIP archive of finished tracks…")
            zip_dir = folder
            created_zip = create_collection_zip(zip_dir, coll_name, finished_files)
            if created_zip:
                self._log(f"ZIP created: {created_zip}")

        parts = [f"{final_done}/{total_selected} downloaded"]
        if final_fail:
            parts.append(f"{final_fail} not found")
        if final_rev:
            parts.append(f"{final_rev} need review")
        summary = ", ".join(parts)

        self._emit("link_progress", ("finished", final_done, total_selected, summary))
        self._emit("link_job_done", (url, kind or "link", 0 if final_fail < total_selected else 1, summary, effective_folder, created_zip))
