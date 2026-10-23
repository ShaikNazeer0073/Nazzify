"""SpotDL Companion - a small Tkinter front-end for an existing spotDL installation."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import companion_core as core

BG, CARD, FG, MUTED, ACCENT = "#121212", "#1e1e1e", "#f2f2f2", "#9a9a9a", "#1db954"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpotDL Companion")
        self.geometry("760x640")
        self.minsize(640, 560)
        self.configure(bg=BG)
        self.cfg = core.load_config()
        self.spotdl = core.find_spotdl(self.cfg.get("spotdl_path", ""))
        self.q = queue.Queue()
        self.holder = {}
        self.busy = False
        self.current = None  # detected song dict
        self.chain = False   # True while "Detect & Download" is running
        self.t0 = None
        self.TIMEOUT = 180   # seconds before a stuck download is stopped
        self._style()
        self._build()
        self.after(100, self._poll)
        self.after(300, self._startup_check)

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
        s.configure("Song.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 13, "bold"))
        s.configure("TButton", font=("Segoe UI", 10), padding=6)
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=8,
                    background=ACCENT, foreground="#000000")
        s.map("Accent.TButton", background=[("active", "#1ed760"), ("disabled", "#2a4a35")])
        s.configure("Green.Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT)

    def _card(self, parent, title):
        outer = ttk.Frame(parent, style="Card.TFrame", padding=12)
        outer.pack(fill="x", pady=6)
        ttk.Label(outer, text=title, style="Muted.TLabel").pack(anchor="w")
        return outer

    def _build(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="SpotDL Companion", style="Title.TLabel").pack(anchor="w")

        c = self._card(root, "1. CURRENT SPOTIFY SONG")
        self.song_var = tk.StringVar(value="No song detected yet")
        self.artist_var = tk.StringVar(value="Play a song in Spotify, then click Detect.")
        ttk.Label(c, textvariable=self.song_var, style="Song.TLabel", wraplength=680).pack(anchor="w", pady=(6, 0))
        ttk.Label(c, textvariable=self.artist_var, style="Card.TLabel", wraplength=680).pack(anchor="w")
        self.one_btn = ttk.Button(c, text="\u26a1  Detect && Download  (one click)", style="Accent.TButton",
                                  command=self.one_click)
        self.one_btn.pack(anchor="w", pady=(10, 0), ipadx=20)
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(anchor="w", pady=(8, 0))
        self.detect_btn = ttk.Button(row, text="Detect Current Song", command=self.detect)
        self.detect_btn.pack(side="left")
        self.dl_cur_btn = ttk.Button(row, text="Download Current Song", style="Accent.TButton",
                                     command=self.download_current, state="disabled")
        self.dl_cur_btn.pack(side="left", padx=8)

        c = self._card(root, "2. OPTIONAL: PASTE A SPOTIFY LINK (track, album or playlist)")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 0))
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var, font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True)
        self.dl_url_btn = ttk.Button(row, text="Download Link", command=self.download_link)
        self.dl_url_btn.pack(side="left", padx=(8, 0))

        c = self._card(root, "3. SETTINGS")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Save to:", style="Card.TLabel").pack(side="left")
        self.folder_var = tk.StringVar(value=self.cfg["download_folder"])
        ttk.Entry(row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse...", command=self.browse_folder).pack(side="left")
        ttk.Button(row, text="Open Folder", command=self.open_folder).pack(side="left", padx=(6, 0))
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="Format:", style="Card.TLabel").pack(side="left")
        self.fmt_var = tk.StringVar(value=self.cfg.get("format", "m4a"))
        cb = ttk.Combobox(row, textvariable=self.fmt_var, values=["m4a", "mp3"], state="readonly", width=8)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_cfg())
        ttk.Label(row, text="m4a = fastest (plays everywhere);  mp3 = slower, most compatible",
                  style="Muted.TLabel").pack(side="left")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="spotDL:", style="Card.TLabel").pack(side="left")
        self.exe_var = tk.StringVar(value=self.spotdl or "(not found - click Browse)")
        ttk.Entry(row, textvariable=self.exe_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse...", command=self.browse_exe).pack(side="left")

        row = ttk.Frame(root)
        row.pack(fill="x", pady=(8, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(row, textvariable=self.status_var).pack(side="left")
        self.cancel_btn = ttk.Button(row, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="right")
        self.elapsed_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.elapsed_var).pack(side="right", padx=10)
        self.bar = ttk.Progressbar(root, style="Green.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=4)

        logf = ttk.Frame(root)
        logf.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(logf, height=10, bg="#0b0b0b", fg="#d0d0d0", insertbackground="#fff",
                           font=("Consolas", 9), wrap="word", state="disabled", relief="flat")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log_msg("Use only for recordings you are authorized to download. "
                     "Audio comes from sources spotDL finds, not from Spotify.")

    # ---------- helpers ----------
    def log_msg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        st = "disabled" if busy else "normal"
        self.detect_btn.configure(state=st)
        self.one_btn.configure(state=st)
        self.dl_url_btn.configure(state=st)
        self.dl_cur_btn.configure(state="disabled" if busy or not self.current else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")

    def save_cfg(self):
        self.cfg["download_folder"] = self.folder_var.get().strip()
        self.cfg["spotdl_path"] = self.spotdl or ""
        self.cfg["format"] = self.fmt_var.get()
        core.save_config(self.cfg)

    def _startup_check(self):
        if self.spotdl:
            self.log_msg(f"Found spotDL: {self.spotdl}")
            threading.Thread(target=lambda: self.q.put(("log", "spotDL version: " + (core.spotdl_version(self.spotdl) or "unknown"))),
                             daemon=True).start()
        else:
            self.status_var.set("spotDL not found.")
            messagebox.showwarning("spotDL not found",
                "I could not find spotDL automatically.\n\nClick the 'Browse...' button next to 'spotDL:' and pick "
                "spotdl.exe (usually in %APPDATA%\\Python\\Python314\\Scripts).\n\n"
                "If you never installed it, open Command Prompt and run:  pip install spotdl")

    # ---------- folder / exe ----------
    def browse_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or os.path.expanduser("~"))
        if d:
            self.folder_var.set(os.path.normpath(d))
            self.save_cfg()

    def open_folder(self):
        folder = self.folder_var.get().strip()
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)  # Windows only
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))

    def browse_exe(self):
        p = filedialog.askopenfilename(title="Select spotdl.exe",
                                       filetypes=[("spotDL", "spotdl.exe"), ("Programs", "*.exe"), ("All", "*.*")])
        if p:
            self.spotdl = os.path.normpath(p)
            self.exe_var.set(self.spotdl)
            self.save_cfg()
            self.log_msg(f"spotDL path set: {self.spotdl}")

    # ---------- detect ----------
    def detect(self):
        self.status_var.set("Detecting current song...")
        self.set_busy(True)
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)

        def work():
            try:
                res = core.get_current_song()
            except Exception as e:  # never crash
                res = {"ok": False, "message": f"Unexpected error: {e}"}
            self.q.put(("detected", res))
        threading.Thread(target=work, daemon=True).start()

    def one_click(self):
        if self.busy:
            return
        if not self._precheck():
            return
        self.chain = True
        self.detect()

    def _on_detected(self, res):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)
        if res.get("ok"):
            self.current = res
            self.song_var.set(res["title"])
            self.artist_var.set(f"{res['artist']}   ({res['status'] or 'unknown state'})")
            note = "" if res["status"].lower() == "playing" else " (Spotify is not currently playing - it may be paused.)"
            self.status_var.set("Song detected." + note)
            self.log_msg(f"Detected: {res['artist']} - {res['title']} [{res['status']}]")
        else:
            self.current = None
            self.song_var.set("No song detected")
            self.artist_var.set(res.get("message", ""))
            self.status_var.set("Detection failed.")
            self.log_msg("Detection failed: " + res.get("message", ""))
        self.set_busy(False)
        if self.chain:
            self.chain = False
            if res.get("ok"):
                self.download_current()

    # ---------- downloads ----------
    def _precheck(self):
        if not self.spotdl or not os.path.isfile(self.spotdl):
            messagebox.showerror("spotDL missing", "spotDL was not found. Use the Browse... button next to 'spotDL:' to select spotdl.exe.")
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

    def download_current(self):
        if not self.current:
            messagebox.showinfo("Nothing detected", "Click 'Detect Current Song' first.")
            return
        folder = self._precheck()
        if folder:
            q = core.search_query(self.current["title"], self.current["artist"])
            self._start(self.spotdl, q, folder, "track", q)

    def download_link(self):
        raw = self.url_var.get().strip()
        kind = core.classify_url(raw)
        if not kind:
            messagebox.showerror("Invalid link", "That doesn't look like a Spotify link.\n\nIt should look like:\n"
                                 "https://open.spotify.com/track/...  (or /album/ or /playlist/)")
            return
        folder = self._precheck()
        if folder:
            self._start(self.spotdl, core.clean_url(raw), folder, kind, raw)

    def _start(self, exe, query, folder, kind, label):
        if self.busy:
            return
        import time
        fmt = self.fmt_var.get()
        cmd = core.build_command(exe, query, folder, kind, fmt)
        plain = core.build_command(exe, query, folder, kind, None)
        self.t0 = time.time()
        self.set_busy(True)
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        self.status_var.set("Downloading...")
        self.log_msg(f"--- Downloading: {label}")
        self.holder = {"cancelled": False}

        def work():
            try:
                rc, text = core.run_download(cmd, lambda l: self.q.put(("line", l)), self.holder)
                if rc != 0 and not self.holder.get("cancelled") and core.looks_like_arg_error(text):
                    self.q.put(("line", "Fast settings not accepted by this spotDL - retrying with default settings..."))
                    rc, text = core.run_download(plain, lambda l: self.q.put(("line", l)), self.holder)
                self.q.put(("done", (rc, text)))
            except FileNotFoundError:
                self.q.put(("fail", "spotDL could not be started. Check the spotDL path."))
            except Exception as e:
                self.q.put(("fail", f"Unexpected error: {e}"))
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

    def _finish(self, ok, msg):
        import time
        if self.t0:
            msg += f" ({int(time.time() - self.t0)}s)"
            self.t0 = None
        self.elapsed_var.set("")
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100 if ok else 0)
        self.status_var.set(msg)
        self.log_msg(msg)
        self.set_busy(False)
        if not ok:
            messagebox.showerror("Download problem", msg)

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log_msg(val)
                elif kind == "detected":
                    self._on_detected(val)
                elif kind == "line":
                    self.log_msg(val)
                    pct = core.parse_progress(val)
                    if pct is not None:
                        self.bar.stop()
                        self.bar.configure(mode="determinate", value=pct)
                    self.status_var.set(val[:90])
                elif kind == "done":
                    rc, text = val
                    if self.holder.get("timeout"):
                        self._finish(False, "Stopped: took longer than 3 minutes. Check your internet and try again, "
                                            "or paste the song's Spotify link.")
                    elif self.holder.get("cancelled"):
                        self._finish(False, "Download cancelled.")
                    elif rc == 0:
                        self._finish(True, "Finished. Click 'Open Folder' to see your music.")
                    else:
                        self._finish(False, core.friendly_error(text, rc))
                elif kind == "fail":
                    self._finish(False, val)
        except queue.Empty:
            pass
        if self.busy and self.t0:
            import time
            el = int(time.time() - self.t0)
            self.elapsed_var.set(f"{el // 60}:{el % 60:02d}")
            if el > self.TIMEOUT and self.holder.get("proc") and not self.holder.get("timeout"):
                self.holder["timeout"] = True
                self.cancel()
        self.after(100, self._poll)


def main():
    try:
        App().mainloop()
    except Exception as e:
        try:
            messagebox.showerror("SpotDL Companion crashed", str(e))
        except Exception:
            print(e)


if __name__ == "__main__":
    main()
