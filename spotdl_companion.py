"""SpotDL Companion - a fast, single-button Windows desktop downloader powered by yt-dlp."""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import companion_core as core

BG, CARD, FG, MUTED, ACCENT = "#121212", "#1e1e1e", "#f2f2f2", "#9a9a9a", "#1db954"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpotDL Companion")
        self.geometry("740x600")
        self.minsize(620, 520)
        self.configure(bg=BG)

        self.cfg = core.load_config()
        self.ytdlp_cmd = core.find_ytdlp(self.cfg.get("spotdl_path", ""))
        self.q = queue.Queue()
        self.holder = {}
        self.busy = False
        self.live_song = None       # Latest live detection dict
        self.download_queue = []    # Queue of song jobs
        self.active_job = None      # Current running job
        self.t0 = None
        self.TIMEOUT = 90           # Per-job timeout in seconds
        self.listener_proc = None

        self._style()
        self._build()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(200, self._startup)

    # ---------- UI ----------
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("Card.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 20, "bold"))
        s.configure("Song.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 14, "bold"))
        s.configure("Artist.TLabel", background=CARD, foreground="#b3b3b3", font=("Segoe UI", 11))
        s.configure("TButton", font=("Segoe UI", 10), padding=6)
        s.configure("Accent.TButton", font=("Segoe UI", 12, "bold"), padding=10,
                    background=ACCENT, foreground="#000000")
        s.map("Accent.TButton", background=[("active", "#1ed760"), ("disabled", "#2a4a35")])
        s.configure("Green.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT)

    def _card(self, parent, title):
        outer = ttk.Frame(parent, style="Card.TFrame", padding=14)
        outer.pack(fill="x", pady=6)
        ttk.Label(outer, text=title, style="Muted.TLabel").pack(anchor="w")
        return outer

    def _build(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="SpotDL Companion", style="Title.TLabel").pack(anchor="w", pady=(0, 4))

        # 1. LIVE NOW PLAYING CARD
        c = self._card(root, "NOW PLAYING")
        self.song_var = tk.StringVar(value="Waiting for Spotify...")
        self.artist_var = tk.StringVar(value="Start playing a song in Spotify.")
        ttk.Label(c, textvariable=self.song_var, style="Song.TLabel", wraplength=660).pack(anchor="w", pady=(6, 2))
        ttk.Label(c, textvariable=self.artist_var, style="Artist.TLabel", wraplength=660).pack(anchor="w")

        # ONE BIG DOWNLOAD BUTTON
        self.dl_btn = ttk.Button(c, text="\u2b07   Download", style="Accent.TButton",
                                 command=self.on_download_click)
        self.dl_btn.pack(fill="x", pady=(12, 4))

        # 2. SETTINGS CARD
        c2 = self._card(root, "SETTINGS")
        row = ttk.Frame(c2, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Save to:", style="Card.TLabel").pack(side="left")
        self.folder_var = tk.StringVar(value=self.cfg["download_folder"])
        ttk.Entry(row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse...", command=self.browse_folder).pack(side="left")
        ttk.Button(row, text="Open Folder", command=self.open_folder).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(c2, style="Card.TFrame")
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Engine:", style="Card.TLabel").pack(side="left")
        downloader_name = os.path.basename(self.ytdlp_cmd[0]) if self.ytdlp_cmd else "None"
        ffmpeg = core.find_ffmpeg()
        ffmpeg_str = "+ FFmpeg" if ffmpeg else "(no FFmpeg)"
        self.engine_var = tk.StringVar(value=f"{downloader_name} {ffmpeg_str}")
        ttk.Label(row2, textvariable=self.engine_var, style="Muted.TLabel").pack(side="left", padx=6)

        # 3. STATUS & PROGRESS
        row3 = ttk.Frame(root)
        row3.pack(fill="x", pady=(8, 2))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(row3, textvariable=self.status_var).pack(side="left")
        self.cancel_btn = ttk.Button(row3, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="right")

        self.bar = ttk.Progressbar(root, style="Green.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(2, 6))

        # 4. LOG AREA
        logf = ttk.Frame(root)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=8, bg="#0b0b0b", fg="#d0d0d0", insertbackground="#fff",
                           font=("Consolas", 9), wrap="word", state="disabled", relief="flat")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        self.log_msg("Ready. Fast downloads via YouTube Music (yt-dlp).")

    # ---------- Helpers ----------
    def log_msg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def save_cfg(self):
        self.cfg["download_folder"] = self.folder_var.get().strip()
        core.save_config(self.cfg)

    def _startup(self):
        if self.ytdlp_cmd:
            self.log_msg(f"Downloader ready: {' '.join(self.ytdlp_cmd)}")
        else:
            self.status_var.set("yt-dlp not found.")
            messagebox.showwarning("Downloader missing",
                                   "Neither yt-dlp nor python -m yt_dlp could be found.\n\n"
                                   "Please run in Command Prompt:\n  pip install -U yt-dlp")

        # Start persistent live media listener process
        proc, _ = core.start_media_listener(
            on_song=lambda res: self.q.put(("now_playing", res)),
            on_error=lambda err: self.q.put(("log", f"Media monitor note: {err}")),
            parent_pid=os.getpid(),
        )
        self.listener_proc = proc

    def _on_close(self):
        if self.listener_proc:
            try:
                self.listener_proc.terminate()
            except OSError:
                pass
        self.cancel()
        self.destroy()

    # ---------- Folder operations ----------
    def browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or os.path.expanduser("~"))
        if d:
            self.folder_var.set(os.path.normpath(d))
            self.save_cfg()

    def open_folder(self):
        folder = self.folder_var.get().strip()
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))

    def _precheck(self):
        if not self.ytdlp_cmd:
            messagebox.showerror("Downloader missing",
                                 "yt-dlp is not available. Run: pip install -U yt-dlp")
            return None
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showerror("No folder", "Please choose a download folder first.")
            return None
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder problem", f"Cannot use that folder:\n{e}")
            return None
        self.save_cfg()
        return folder

    # ---------- Download & Queue Logic ----------
    def on_download_click(self):
        folder = self._precheck()
        if not folder:
            return

        # Capture song at click time
        song = self.live_song
        if not song or not song.get("ok"):
            # Check immediately in case live stream was in transition
            fresh = core.get_current_song()
            if fresh.get("ok"):
                song = fresh
                self.live_song = fresh
                self._on_now_playing(fresh)
            else:
                msg = fresh.get("message") or (song.get("message") if song else "No song detected from Spotify yet. Play a song first.")
                self.status_var.set(msg)
                self.log_msg(msg)
                return

        artist = song["artist"]
        title = song["title"]
        duration = song.get("duration", 0)
        key = f"{artist.strip().lower()} - {title.strip().lower()}"

        # Skip if file already exists
        if core.song_file_exists(folder, artist, title):
            self.status_var.set(f"Already downloaded: {artist} - {title}")
            self.log_msg(f"Skipped: '{artist} - {title}' already exists in download folder.")
            return

        # Check if currently downloading
        if self.active_job and self.active_job["key"] == key:
            self.status_var.set(f"Already downloading: {artist} - {title}")
            return

        # Don't queue the same song twice
        if any(j["key"] == key for j in self.download_queue):
            self.status_var.set(f"Already queued: {artist} - {title}")
            return

        job = {
            "artist": artist,
            "title": title,
            "duration": duration,
            "folder": folder,
            "key": key,
        }
        self.download_queue.append(job)

        if not self.busy:
            self._process_next_job()
        else:
            q_len = len(self.download_queue)
            self.status_var.set(f"Queued: {artist} - {title} ({q_len} waiting)")
            self.log_msg(f"Queued: {artist} - {title} ({q_len} in queue)")

    def _process_next_job(self):
        if self.busy:
            return
        if not self.download_queue:
            self.active_job = None
            self.cancel_btn.configure(state="disabled")
            self.bar.stop()
            self.bar.configure(mode="determinate", value=0)
            return

        job = self.download_queue.pop(0)

        # Skip if already downloaded while waiting in queue
        if core.song_file_exists(job["folder"], job["artist"], job["title"]):
            self.log_msg(f"Skipped (already exists): {job['artist']} - {job['title']}")
            self._process_next_job()
            return

        self.active_job = job
        self.busy = True
        self.cancel_btn.configure(state="normal")
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        self.t0 = time.time()
        self.holder = {"proc": None, "cancelled": False, "timeout": False}

        q_info = f" ({len(self.download_queue)} waiting)" if self.download_queue else ""
        self.status_var.set(f"Downloading: {job['artist']} - {job['title']}{q_info}")
        self.log_msg(f"--- Downloading: {job['artist']} - {job['title']}{q_info}")

        cmd, base = core.build_ytdlp_command(
            self.ytdlp_cmd, job["artist"], job["title"], job["folder"], job["duration"]
        )

        def work():
            try:
                rc, text = core.run_download(cmd, lambda l: self.q.put(("line", l)), self.holder)
                self.q.put(("job_done", (job, rc, text)))
            except Exception as e:
                self.q.put(("job_fail", (job, str(e))))

        threading.Thread(target=work, daemon=True).start()

    def cancel(self):
        p = self.holder.get("proc")
        if p and p.poll() is None:
            self.holder["cancelled"] = True
            try:
                p.terminate()
            except OSError:
                pass
            self.log_msg("Cancel requested...")

    # ---------- Event Loop & Handlers ----------
    def _on_now_playing(self, res):
        self.live_song = res
        if res.get("ok"):
            dur = res.get("duration", 0)
            dur_str = f" ({dur // 60}:{dur % 60:02d})" if dur else ""
            self.song_var.set(res["title"])
            self.artist_var.set(f"{res['artist']}{dur_str}  [{res.get('status', 'Playing')}]")
        else:
            self.song_var.set("No song playing")
            self.artist_var.set(res.get("message", "Start playing a song in Spotify."))

    def _on_job_done(self, job, rc, text):
        elapsed = int(time.time() - self.t0) if self.t0 else 0
        self.t0 = None

        if self.holder.get("timeout"):
            msg = f"Timed out (90s): {job['artist']} - {job['title']}"
            self.status_var.set(msg)
            self.log_msg(msg)
        elif self.holder.get("cancelled"):
            msg = f"Cancelled: {job['artist']} - {job['title']}"
            self.status_var.set(msg)
            self.log_msg(msg)
        else:
            file_exists = core.song_file_exists(job["folder"], job["artist"], job["title"])
            if (rc in (0, 101)) and file_exists:
                msg = f"Finished in {elapsed}s: {job['artist']} - {job['title']}"
                self.status_var.set(msg)
                self.log_msg(f"SUCCESS ({elapsed}s): {job['artist']} - {job['title']}")
                self.bar.stop()
                self.bar.configure(mode="determinate", value=100)
            elif not file_exists:
                if "does not pass filter" in text:
                    msg = f"No match within duration +/-12s for '{job['title']}'."
                else:
                    msg = f"Download ended without file for '{job['title']}'."
                self.status_var.set(msg)
                self.log_msg(msg)
                self.bar.stop()
                self.bar.configure(mode="determinate", value=0)
            else:
                err = core.friendly_error(text, rc)
                self.status_var.set(err)
                self.log_msg(f"Error ({rc}): {err}")
                self.bar.stop()
                self.bar.configure(mode="determinate", value=0)

        self.busy = False
        self._process_next_job()

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log_msg(val)
                elif kind == "now_playing":
                    self._on_now_playing(val)
                elif kind == "line":
                    self.log_msg(val)
                    pct = core.parse_progress(val)
                    if pct is not None:
                        self.bar.stop()
                        self.bar.configure(mode="determinate", value=pct)
                        if self.active_job:
                            q_info = f" ({len(self.download_queue)} waiting)" if self.download_queue else ""
                            self.status_var.set(f"Downloading: {self.active_job['title']} - {pct}%{q_info}")
                elif kind == "job_done":
                    job, rc, text = val
                    self._on_job_done(job, rc, text)
                elif kind == "job_fail":
                    job, err = val
                    self.log_msg(f"Download failed to start for {job['title']}: {err}")
                    self.status_var.set(f"Error: {err}")
                    self.busy = False
                    self._process_next_job()
        except queue.Empty:
            pass

        # Per-job timeout check
        if self.busy and self.t0:
            el = int(time.time() - self.t0)
            if el > self.TIMEOUT and self.holder.get("proc") and not self.holder.get("timeout"):
                self.holder["timeout"] = True
                self.cancel()

        self.after(100, self._poll)


def main():
    try:
        App().mainloop()
    except Exception as e:
        try:
            messagebox.showerror("SpotDL Companion error", str(e))
        except Exception:
            print(e)


if __name__ == "__main__":
    main()
