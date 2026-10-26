"""
Nazzify - Web-styled Spotify Companion interface.
Standard library HTTP server on 127.0.0.1 with random port and token authentication.
Reuses companion_core.py and playlist_downloader.py.
"""
from __future__ import annotations

import glob
import http.server
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from typing import Any

import companion_core as core
from playlist_downloader import PlaylistDownloader, create_collection_zip, fetch_collection

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")
AUTH_TOKEN = secrets.token_hex(16)


# ══════════════════════════════════════════════════════════════════════════════
# Backend Engine
# ══════════════════════════════════════════════════════════════════════════════
class NazzifyEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = core.load_config()
        self.ytdlp_cmd = core.find_ytdlp(self.cfg.get("spotdl_path", ""))
        self.spotdl_exe = core.find_spotdl(self.cfg.get("spotdl_path", ""))
        self.ffmpeg = core.find_ffmpeg()

        # Live Now Playing
        self.live_song: dict[str, Any] = {
            "ok": False,
            "title": "",
            "artist": "",
            "status": "Stopped",
            "duration": 0,
            "thumbnail": "",
            "message": "Waiting for Spotify..."
        }
        self.listener_proc: subprocess.Popen | None = None

        # Now Playing Download Queue
        self.download_queue: list[dict[str, Any]] = []
        self.active_job: dict[str, Any] | None = None
        self.single_holder: dict[str, Any] = {}
        self.single_busy = False
        self.single_progress = 0
        self.t0: float | None = None
        self.TIMEOUT = 90

        # Link / Playlist Downloader
        self.link_busy = False
        self.link_cancelled = False
        self.current_collection: dict[str, Any] | None = None
        self.track_data: dict[str, dict[str, Any]] = {}
        self.playlist_dl: PlaylistDownloader | None = None
        self.playlist_q: queue.Queue = queue.Queue()
        self.last_effective_folder = ""
        self.last_created_zip = ""
        self.last_finished_files: list[str] = []

        # Status & Logs
        self.status_text = "Ready."
        self.progress_pct = 0
        self.done_summary = ""
        self.done_folder = ""
        self.done_zip = ""
        self.logs: list[str] = []

        # Lifecycle
        self.shutting_down = False
        self.last_poll_time = time.time()
        self.has_connected = False

        self._start_media_listener()
        self._start_playlist_queue_reader()

    def log(self, msg: str):
        with self.lock:
            self.logs.append(msg)
            if len(self.logs) > 300:
                self.logs.pop(0)

    # ── Media Listener ────────────────────────────────────────────────────────
    def _start_media_listener(self):
        try:
            proc, _ = core.start_media_listener(
                on_song=self._on_live_song,
                on_error=lambda err: self.log(f"Media monitor note: {err}"),
                parent_pid=os.getpid(),
            )
            self.listener_proc = proc
        except Exception as e:
            self.log(f"Could not start media listener: {e}")

    def _on_live_song(self, res: dict[str, Any]):
        with self.lock:
            self.live_song = res

    # ── Single / Queue Downloads ──────────────────────────────────────────────
    def queue_now_playing(self) -> dict[str, Any]:
        with self.lock:
            song = dict(self.live_song)
            folder = self.cfg.get("download_folder", "").strip()

        if not folder:
            return {"ok": False, "error": "Please set a download folder first."}
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"Invalid download folder: {e}"}

        if not song.get("ok"):
            fresh = core.get_current_song()
            if fresh.get("ok"):
                with self.lock:
                    self.live_song = fresh
                song = fresh
            else:
                msg = fresh.get("message") or song.get("message") or "No song detected from Spotify yet. Play a song first."
                self.status_text = msg
                self.log(msg)
                return {"ok": False, "error": msg}

        artist = song.get("artist", "").strip()
        title = song.get("title", "").strip()
        duration = song.get("duration", 0)
        key = f"{artist.lower()} - {title.lower()}"

        if core.song_file_exists(folder, artist, title):
            msg = f"Already downloaded: {artist} - {title}"
            self.status_text = msg
            self.log(f"Skipped: '{artist} - {title}' already exists in download folder.")
            return {"ok": True, "message": msg, "already_exists": True}

        with self.lock:
            if self.active_job and self.active_job["key"] == key:
                return {"ok": True, "message": f"Already downloading: {artist} - {title}"}
            if any(j["key"] == key for j in self.download_queue):
                return {"ok": True, "message": f"Already queued: {artist} - {title}"}

            job = {
                "artist": artist,
                "title": title,
                "duration": duration,
                "thumbnail": song.get("thumbnail", ""),
                "folder": folder,
                "key": key,
            }
            self.download_queue.append(job)
            q_len = len(self.download_queue)

        self.log(f"Queued: {artist} - {title} ({q_len} in queue)")
        self._process_next_job()
        return {"ok": True, "message": f"Queued {title}"}

    def _process_next_job(self):
        with self.lock:
            if self.single_busy:
                return
            if not self.download_queue:
                self.active_job = None
                self.progress_pct = 0
                return

            job = self.download_queue.pop(0)

        # Check existing on disk before starting
        if core.song_file_exists(job["folder"], job["artist"], job["title"]):
            self.log(f"Skipped (already exists): {job['artist']} - {job['title']}")
            self._process_next_job()
            return

        with self.lock:
            self.active_job = job
            self.single_busy = True
            self.single_progress = 0
            self.t0 = time.time()
            self.single_holder = {"proc": None, "cancelled": False, "timeout": False}

        q_info = f" ({len(self.download_queue)} waiting)" if self.download_queue else ""
        self.status_text = f"Downloading: {job['artist']} - {job['title']}{q_info}"
        self.log(f"--- Downloading: {job['artist']} - {job['title']}{q_info}")

        cmd, base = core.build_ytdlp_command(
            self.ytdlp_cmd, job["artist"], job["title"], job["folder"], job["duration"]
        )

        def work():
            try:
                rc, text = core.run_download(
                    cmd,
                    self._on_single_line,
                    self.single_holder,
                )
                self._on_single_done(job, rc, text)
            except Exception as e:
                self.log(f"Download failed to start for {job['title']}: {e}")
                with self.lock:
                    self.single_busy = False
                    self.active_job = None
                self._process_next_job()

        threading.Thread(target=work, daemon=True).start()

    def _on_single_line(self, line: str):
        self.log(line)
        pct = core.parse_progress(line)
        if pct is not None:
            with self.lock:
                self.single_progress = pct
                self.progress_pct = pct
                if self.active_job:
                    q_info = f" ({len(self.download_queue)} waiting)" if self.download_queue else ""
                    self.status_text = f"Downloading: {self.active_job['title']} - {pct}%{q_info}"

    def _on_single_done(self, job: dict[str, Any], rc: int, text: str):
        elapsed = int(time.time() - (self.t0 or time.time()))
        self.t0 = None

        if self.single_holder.get("timeout"):
            msg = f"Timed out (90s): {job['artist']} - {job['title']}"
            self.status_text = msg
            self.log(msg)
        elif self.single_holder.get("cancelled"):
            msg = f"Cancelled: {job['artist']} - {job['title']}"
            self.status_text = msg
            self.log(msg)
        else:
            file_path = core.find_audio_file(job["folder"], job["artist"], job["title"])
            if (rc in (0, 101)) and file_path:
                thumb = job.get("thumbnail", "")
                if thumb and os.path.isfile(thumb):
                    if core.embed_spotify_cover(file_path, thumb):
                        self.log(f"Cover art: Embedded exact Spotify thumbnail ({os.path.basename(thumb)})")
                msg = f"Finished in {elapsed}s: {job['artist']} - {job['title']}"
                self.status_text = msg
                self.log(f"SUCCESS ({elapsed}s): {job['artist']} - {job['title']}")
                self.progress_pct = 100
                self.done_folder = job["folder"]
                self.done_summary = f"Done: {job['title']}"
            elif not file_path:
                if "does not pass filter" in text:
                    msg = f"No match within duration +/-12s for '{job['title']}'."
                else:
                    msg = f"Download ended without file for '{job['title']}'."
                self.status_text = msg
                self.log(msg)
                self.progress_pct = 0
            else:
                err = core.friendly_error(text, rc)
                self.status_text = err
                self.log(f"Error ({rc}): {err}")
                self.progress_pct = 0

        with self.lock:
            self.single_busy = False
            self.active_job = None

        self._process_next_job()

    # ── Link / Playlist Queue Reader ──────────────────────────────────────────
    def _start_playlist_queue_reader(self):
        def reader():
            while True:
                try:
                    kind, val = self.playlist_q.get()
                    self._handle_playlist_msg(kind, val)
                except Exception as e:
                    self.log(f"Playlist event error: {e}")

        threading.Thread(target=reader, daemon=True).start()

    def _handle_playlist_msg(self, kind: str, val: Any):
        if kind == "link_line":
            self.log(val)
        elif kind == "link_track_status":
            tid, status_text = val
            with self.lock:
                if tid in self.track_data:
                    # Map review status to Done per specification: "No review status"
                    if "review" in status_text.lower() or "⚠" in status_text:
                        mapped_status = "Done"
                    elif "Done" in status_text or "✓" in status_text:
                        mapped_status = "Done"
                    elif "Downloading" in status_text:
                        mapped_status = "Downloading"
                    elif "Not found" in status_text or "Failed" in status_text or "✗" in status_text:
                        mapped_status = "Not found"
                    else:
                        mapped_status = "Waiting"
                    self.track_data[tid]["status"] = mapped_status
        elif kind == "link_progress":
            status, done_n, total, label = val
            # Sanitize review mentions in label
            label = label.replace("needs review", "").replace("need review", "")
            self.status_text = label
            if total > 0:
                self.progress_pct = int((done_n / total) * 100)
        elif kind == "zip_created":
            self.last_created_zip = val
            self.done_zip = val
            self.status_text = f"ZIP created: {os.path.basename(val)}"
            self.log(f"ZIP created: {val}")
        elif kind == "link_job_done":
            url, lkind, rc, summary, *extra = val
            effective_folder = extra[0] if len(extra) > 0 else ""
            created_zip = extra[1] if len(extra) > 1 else None
            finished_files = extra[2] if len(extra) > 2 else []
            was_cancelled = extra[3] if len(extra) > 3 else False
            coll_name = extra[4] if len(extra) > 4 else ""

            with self.lock:
                self.link_busy = False
                self.last_effective_folder = effective_folder
                self.last_created_zip = created_zip or ""
                self.last_finished_files = finished_files
                self.done_folder = effective_folder
                self.done_zip = created_zip or ""

            kind_label = {"track": "Track", "album": "Album", "playlist": "Playlist"}.get(lkind, lkind.capitalize())
            if was_cancelled or self.link_cancelled:
                self.status_text = f"Cancelled: {summary}"
                self.log(f"Cancelled: {kind_label} — {summary} — {url}")
            elif rc == 0:
                clean_sum = summary.replace("need review", "").strip(" ,")
                self.status_text = f"Done: {clean_sum} ✓"
                self.done_summary = f"Done: {clean_sum}"
                self.log(f"SUCCESS: {kind_label} — {clean_sum} — {url}")
                self.progress_pct = 100
            else:
                self.status_text = f"Complete: {summary}"
                self.log(f"Completed with notes: {kind_label} — {summary} — {url}")
        elif kind == "link_job_fail":
            url, err = val
            with self.lock:
                self.link_busy = False
            self.status_text = f"Error: {err[:80]}"
            self.log(f"Link download error: {err}")

    # ── Link Actions ──────────────────────────────────────────────────────────
    def fetch_link_async(self, url: str) -> dict[str, Any]:
        url = url.strip()
        kind, sid = core.parse_spotify_url(url)
        if not kind:
            return {"ok": False, "error": "Invalid Spotify URL. Expected https://open.spotify.com/..."}

        self.status_text = f"Fetching {kind} metadata from Spotify..."
        self.log(f"Fetching Spotify collection: {url}")

        def work():
            try:
                coll = fetch_collection(self.spotdl_exe, url)
                if not coll or not coll.get("tracks"):
                    self.status_text = "Failed to fetch Spotify collection."
                    self.log(f"Failed to fetch metadata for: {url}")
                    return

                with self.lock:
                    self.current_collection = coll
                    tracks = coll.get("tracks", [])
                    kind_type = coll.get("kind", "album")
                    name = coll.get("name", "Spotify")

                    folder = self.cfg.get("download_folder", "").strip()
                    if kind_type in ("album", "playlist"):
                        eff_folder = os.path.join(folder, core.sanitize_filename(name))
                    else:
                        eff_folder = folder

                    self.track_data = {}
                    for idx, t in enumerate(tracks):
                        tid = t.get("song_id") or f"track_{idx}"
                        title = t.get("name", "")
                        art_list = t.get("artists", [])
                        artist_str = ", ".join(art_list) if isinstance(art_list, list) else str(art_list)
                        exists = core.song_file_exists(eff_folder, artist_str, title)

                        self.track_data[tid] = {
                            "num": idx + 1,
                            "checked": not exists,
                            "status": "Have" if exists else "Waiting",
                            "title": title,
                            "artists": artist_str,
                            "duration": t.get("duration", 0),
                            "cover_url": t.get("cover_url", ""),
                            "song_id": tid,
                            "raw": t,
                        }

                total = len(tracks)
                self.status_text = f"Loaded {total} tracks from {kind.capitalize()}."
                self.log(f"Fetched {total} tracks from Spotify: {name}")
            except Exception as e:
                self.status_text = f"Error fetching link: {e}"
                self.log(f"Error fetching collection: {e}")

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "message": "Fetching metadata..."}

    def download_selected(self, selected_ids: list[str], options: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if not self.current_collection:
                return {"ok": False, "error": "No collection loaded."}
            if self.link_busy:
                return {"ok": False, "error": "A download is already in progress."}

            folder = self.cfg.get("download_folder", "").strip()
            if not folder:
                return {"ok": False, "error": "Please set a download folder first."}

            if not selected_ids:
                return {"ok": False, "error": "Please select at least one track."}

            self.link_busy = True
            self.link_cancelled = False
            self.done_folder = ""
            self.done_zip = ""
            self.done_summary = ""

            for tid in selected_ids:
                if tid in self.track_data:
                    if self.track_data[tid]["status"] not in ("Done", "Have"):
                        self.track_data[tid]["status"] = "Waiting"

            url = self.current_collection.get("url") or options.get("url", "")
            # If collection doesn't hold original url, use the tracks
            if not url:
                url = f"https://open.spotify.com/{self.current_collection.get('kind', 'album')}/{self.current_collection.get('tracks', [{}])[0].get('song_id', '')}"

            coll = self.current_collection

        opts = {
            "subfolder": options.get("subfolder", True),
            "create_zip": options.get("create_zip", False),
        }

        self.playlist_dl = PlaylistDownloader(self.ytdlp_cmd, self.spotdl_exe, self.playlist_q)
        self.playlist_dl.start(url, folder, selected_ids, opts, collection_info=coll)
        self.log(f"Started download for {len(selected_ids)} selected tracks...")
        return {"ok": True, "message": f"Downloading {len(selected_ids)} tracks..."}

    def cancel_all(self):
        with self.lock:
            # Cancel single
            if self.single_holder and self.single_holder.get("proc"):
                self.single_holder["cancelled"] = True
                try:
                    self.single_holder["proc"].terminate()
                except OSError:
                    pass
            # Cancel link
            if self.playlist_dl:
                self.link_cancelled = True
                self.playlist_dl.stop()

        self.status_text = "Cancelling..."
        self.log("Cancel requested...")

    # ── Folder & Filesystem Actions ───────────────────────────────────────────
    def browse_folder(self) -> str:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            initial = self.cfg.get("download_folder") or os.path.expanduser("~")
            chosen = filedialog.askdirectory(initialdir=initial, parent=root)
            root.destroy()
            if chosen:
                norm = os.path.normpath(chosen)
                with self.lock:
                    self.cfg["download_folder"] = norm
                    core.save_config(self.cfg)
                self.log(f"Download folder changed to: {norm}")
                return norm
        except Exception as e:
            self.log(f"Folder browser error: {e}")
        return self.cfg.get("download_folder", "")

    def open_folder(self, folder: str = ""):
        target = folder or self.done_folder or self.cfg.get("download_folder", "")
        if target and os.path.isdir(target):
            try:
                os.startfile(target)
            except Exception as e:
                self.log(f"Cannot open folder: {e}")

    def show_zip(self, zip_path: str = ""):
        target = zip_path or self.done_zip or self.last_created_zip
        if target and os.path.isfile(target):
            try:
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(target)}"])
            except Exception as e:
                self.log(f"Cannot highlight zip: {e}")

    def save_folder_config(self, folder: str):
        if folder:
            norm = os.path.normpath(folder.strip())
            with self.lock:
                self.cfg["download_folder"] = norm
                core.save_config(self.cfg)

    # ── State Snapshot for Polling ────────────────────────────────────────────
    def get_state(self) -> dict[str, Any]:
        with self.lock:
            self.last_poll_time = time.time()
            self.has_connected = True

            # Format Now Playing
            song = dict(self.live_song)
            has_thumb = bool(song.get("thumbnail") and os.path.isfile(song.get("thumbnail", "")))

            # Format Queue
            active_info = None
            if self.active_job:
                active_info = {
                    "title": self.active_job["title"],
                    "artist": self.active_job["artist"],
                    "progress": self.single_progress,
                }
            waiting_queue = [
                {"title": j["title"], "artist": j["artist"]}
                for j in self.download_queue
            ]

            # Format Link Collection & Tracks
            coll_info = None
            if self.current_collection:
                coll_info = {
                    "name": self.current_collection.get("name", ""),
                    "owner": self.current_collection.get("owner", ""),
                    "kind": self.current_collection.get("kind", "album"),
                    "cover_url": self.current_collection.get("cover_url", ""),
                    "total": len(self.track_data),
                }

            tracks_list = [
                {
                    "tid": tid,
                    "num": d["num"],
                    "checked": d["checked"],
                    "status": d["status"],
                    "title": d["title"],
                    "artists": d["artists"],
                    "duration": d["duration"],
                }
                for tid, d in self.track_data.items()
            ]

            is_busy = self.single_busy or self.link_busy

            return {
                "now_playing": {
                    "ok": song.get("ok", False),
                    "title": song.get("title", ""),
                    "artist": song.get("artist", ""),
                    "duration": song.get("duration", 0),
                    "status": song.get("status", "Stopped"),
                    "has_thumbnail": has_thumb,
                    "message": song.get("message", ""),
                },
                "queue": {
                    "active": active_info,
                    "waiting": waiting_queue,
                },
                "link": {
                    "busy": self.link_busy,
                    "collection": coll_info,
                    "tracks": tracks_list,
                },
                "status_bar": {
                    "status": self.status_text,
                    "progress": self.progress_pct,
                    "can_cancel": is_busy,
                    "done_summary": self.done_summary,
                    "done_folder": self.done_folder,
                    "done_zip": self.done_zip,
                },
                "config": {
                    "download_folder": self.cfg.get("download_folder", ""),
                },
                "engine": {
                    "downloader": os.path.basename(self.ytdlp_cmd[0]) if self.ytdlp_cmd else "None",
                    "ffmpeg": bool(self.ffmpeg),
                },
                "logs": list(self.logs[-80:]),
            }

    def shutdown(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.log("Shutting down Nazzify...")

        if self.listener_proc:
            try:
                self.listener_proc.terminate()
            except Exception:
                pass

        if self.single_holder and self.single_holder.get("proc"):
            try:
                self.single_holder["proc"].terminate()
            except Exception:
                pass

        if self.playlist_dl:
            try:
                self.playlist_dl.stop()
            except Exception:
                pass

        threading.Thread(target=lambda: (time.sleep(0.4), os._exit(0)), daemon=True).start()


# Global engine instance
ENGINE = NazzifyEngine()


# ══════════════════════════════════════════════════════════════════════════════
# HTTP Request Handler with Token Security
# ══════════════════════════════════════════════════════════════════════════════
class NazzifyHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default standard HTTP logging
        return

    def _verify_token(self) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = qs.get("token", [""])[0]
        if not token:
            token = self.headers.get("X-Auth-Token", "")
        return token == AUTH_TOKEN

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. API Endpoints
        if path.startswith("/api/"):
            if not self._verify_token():
                self._send_json({"error": "Unauthorized token"}, 401)
                return

            if path == "/api/poll":
                self._send_json(ENGINE.get_state())
                return

            if path == "/api/thumbnail":
                thumb_path = ENGINE.live_song.get("thumbnail", "")
                if thumb_path and os.path.isfile(thumb_path):
                    try:
                        with open(thumb_path, "rb") as f:
                            content = f.read()
                        ext = os.path.splitext(thumb_path)[1].lower()
                        mime = "image/png" if ext == ".png" else "image/jpeg"
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(len(content)))
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(content)
                        return
                    except Exception:
                        pass
                self.send_response(404)
                self.end_headers()
                return

            self._send_json({"error": "Not found"}, 404)
            return

        # 2. Static Files from ui/
        rel_path = path.lstrip("/")
        if not rel_path or rel_path == "":
            rel_path = "index.html"

        # Prevent directory traversal
        full_path = os.path.normpath(os.path.join(UI_DIR, rel_path))
        if not full_path.startswith(UI_DIR) or not os.path.isfile(full_path):
            self.send_response(404)
            self.end_headers()
            return

        mime_type, _ = mimetypes.guess_type(full_path)
        if full_path.endswith(".ttf"):
            mime_type = "font/ttf"
        elif full_path.endswith(".woff2"):
            mime_type = "font/woff2"

        try:
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            if mime_type:
                self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_response(500)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self._verify_token():
            self._send_json({"error": "Unauthorized token"}, 401)
            return

        # Parse JSON body
        length = int(self.headers.get("Content-Length", 0))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}

        if path == "/api/download_now_playing":
            res = ENGINE.queue_now_playing()
            self._send_json(res)
            return

        if path == "/api/fetch_link":
            url = body.get("url", "")
            res = ENGINE.fetch_link_async(url)
            self._send_json(res)
            return

        if path == "/api/download_selected":
            selected_ids = body.get("selected_ids", [])
            options = body.get("options", {})
            res = ENGINE.download_selected(selected_ids, options)
            self._send_json(res)
            return

        if path == "/api/cancel":
            ENGINE.cancel_all()
            self._send_json({"ok": True, "message": "Cancelling..."})
            return

        if path == "/api/browse_folder":
            chosen = ENGINE.browse_folder()
            self._send_json({"ok": True, "folder": chosen})
            return

        if path == "/api/open_folder":
            folder = body.get("folder", "")
            ENGINE.open_folder(folder)
            self._send_json({"ok": True})
            return

        if path == "/api/show_zip":
            zip_p = body.get("zip_path", "")
            ENGINE.show_zip(zip_p)
            self._send_json({"ok": True})
            return

        if path == "/api/save_config":
            folder = body.get("download_folder", "")
            ENGINE.save_folder_config(folder)
            self._send_json({"ok": True})
            return

        if path == "/api/exit":
            self._send_json({"ok": True})
            ENGINE.shutdown()
            return

        self._send_json({"error": "Not found"}, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Window Launcher & Fallback (pywebview / Edge App Mode)
# ══════════════════════════════════════════════════════════════════════════════
def launch_edge_app_mode(url: str):
    """Fallback with no installs: launch Edge in app mode (--app=URL)."""
    edge_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge_exe = next((p for p in edge_candidates if os.path.isfile(p)), shutil.which("msedge") or "msedge")

    cmd = [edge_exe, f"--app={url}", "--new-window"]
    try:
        proc = subprocess.Popen(cmd)
        # Monitor process termination in thread
        def watch_edge():
            proc.wait()
            ENGINE.shutdown()
        threading.Thread(target=watch_edge, daemon=True).start()
    except Exception as e:
        print(f"Could not launch Edge app mode: {e}")
        # As last resort, open standard browser
        import webbrowser
        webbrowser.open(url)


def main():
    # 1. Start HTTP Server on random free port
    server = socketserver.TCPServer(("127.0.0.1", 0), NazzifyHandler)
    host, port = server.server_address
    app_url = f"http://127.0.0.1:{port}/?token={AUTH_TOKEN}"

    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    print(f"Nazzify server running at {app_url}")

    # 2. Watchdog: if window closes without sending exit, detect poll timeout
    def watchdog():
        while not ENGINE.shutting_down:
            time.sleep(1.0)
            if ENGINE.has_connected:
                # If page was polling and hasn't polled for > 4.5 seconds, window was closed
                if time.time() - ENGINE.last_poll_time > 4.5:
                    print("Watchdog: Frontend disconnected, shutting down.")
                    ENGINE.shutdown()
                    break

    threading.Thread(target=watchdog, daemon=True).start()

    # 3. Launch window via pywebview if available, fallback to Edge app mode
    use_pywebview = True
    try:
        import webview
    except ImportError:
        use_pywebview = False

    if use_pywebview:
        try:
            window = webview.create_window(
                "Nazzify",
                app_url,
                width=1060,
                height=760,
                min_size=(900, 640),
                background_color="#0A0A0D",
            )
            window.events.closed += ENGINE.shutdown
            webview.start()
        except Exception as err:
            print(f"pywebview failed: {err}. Falling back to Edge app mode...")
            launch_edge_app_mode(app_url)
            # Keep main thread alive while Edge runs
            while not ENGINE.shutting_down:
                time.sleep(0.5)
    else:
        print("pywebview not installed. Running in Edge app mode...")
        launch_edge_app_mode(app_url)
        while not ENGINE.shutting_down:
            time.sleep(0.5)

    ENGINE.shutdown()


if __name__ == "__main__":
    main()
