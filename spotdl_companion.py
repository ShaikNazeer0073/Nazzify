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
from playlist_downloader import PlaylistDownloader

from PIL import Image, ImageTk, ImageDraw, ImageFont

BG = "#0B0712"
PANEL = "#15101F"
CARD = "#1C1530"
BORDER = "#2A2140"
ACCENT = "#8B5CF6"
ACCENT_HOVER = "#A78BFA"
ACCENT_DARK = "#6D28D9"
TEXT = "#F4F0FF"
MUTED = "#9A90B5"
SUCCESS = "#34D399"
WARNING = "#FBBF24"
ERROR = "#F87171"


class RoundedButton(tk.Canvas):
    """
    Smooth, anti-aliased rounded button drawn on Tkinter Canvas using Pillow.
    Supports primary (purple), secondary (card/dark), outline, and danger styles.
    """
    def __init__(self, parent, text="", command=None, width=120, height=36, radius=8,
                 style="primary", parent_bg=CARD, font=("Segoe UI", 10, "bold"),
                 cursor="hand2", **kwargs):
        self.parent_bg = parent_bg
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, **kwargs)
        self.text = text
        self.command = command
        self.radius = radius
        self.btn_style = style
        self.font = font
        self.btn_cursor = cursor
        self.state = "normal"
        self._hover = False
        self._pressed = False
        self._img_cache = {}

        self._apply_style(style)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_configure)
        self.redraw()

    def _apply_style(self, style):
        if style == "primary":
            self.bg_color = ACCENT
            self.hover_color = ACCENT_HOVER
            self.active_color = ACCENT_DARK
            self.disabled_color = "#2A2140"
            self.text_color = "#FFFFFF"
            self.disabled_text = "#6B6280"
            self.border_color = None
        elif style == "secondary":
            self.bg_color = "#2A2140"
            self.hover_color = "#3B3058"
            self.active_color = "#201833"
            self.disabled_color = "#1C1530"
            self.text_color = "#F4F0FF"
            self.disabled_text = "#6B6280"
            self.border_color = None
        elif style == "outline":
            self.bg_color = self.parent_bg
            self.hover_color = "#2A2140"
            self.active_color = "#1F1830"
            self.disabled_color = self.parent_bg
            self.text_color = "#F4F0FF"
            self.disabled_text = "#6B6280"
            self.border_color = "#2A2140"
        elif style == "danger":
            self.bg_color = "#7F1D1D"
            self.hover_color = "#991B1B"
            self.active_color = "#581414"
            self.disabled_color = "#2A2140"
            self.text_color = "#FEE2E2"
            self.disabled_text = "#6B6280"
            self.border_color = None

    def _on_configure(self, _e):
        self.redraw()

    def _on_enter(self, _e):
        if self.state == "normal":
            self._hover = True
            super().configure(cursor=self.btn_cursor)
            self.redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._pressed = False
        self.redraw()

    def _on_press(self, _e):
        if self.state == "normal":
            self._pressed = True
            self.redraw()

    def _on_release(self, _e):
        if self.state == "normal":
            was_pressed = self._pressed
            self._pressed = False
            self.redraw()
            if was_pressed and self.command:
                try:
                    self.command()
                except Exception as err:
                    print(f"Button command error: {err}")

    def configure_state(self, state):
        self.state = state
        super().configure(cursor=self.btn_cursor if state == "normal" else "arrow")
        self.redraw()

    def set_text(self, text):
        self.text = text
        self.redraw()

    def configure(self, cnf=None, **kwargs):
        if cnf is not None:
            kwargs.update(cnf)
        if "state" in kwargs:
            self.configure_state(kwargs.pop("state"))
        if "text" in kwargs:
            self.set_text(kwargs.pop("text"))
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def _get_bg_img(self, w, h, fill_col, border_col=None):
        key = (w, h, self.radius, fill_col, border_col)
        if key in self._img_cache:
            return self._img_cache[key]
        scale = 2
        im = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        rect = [(1, 1), (w * scale - 2, h * scale - 2)]
        d.rounded_rectangle(rect, radius=self.radius * scale, fill=fill_col,
                            outline=border_col, width=scale if border_col else 0)
        im = im.resize((w, h), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(im)
        self._img_cache[key] = photo
        return photo

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), int(self["width"]))
        h = max(self.winfo_height(), int(self["height"]))
        if w <= 1 or h <= 1:
            return
        if self.state == "disabled":
            fill = self.disabled_color
            tcol = self.disabled_text
            border = None
        elif self._pressed:
            fill = self.active_color
            tcol = self.text_color
            border = self.border_color
        elif self._hover:
            fill = self.hover_color
            tcol = self.text_color
            border = self.border_color
        else:
            fill = self.bg_color
            tcol = self.text_color
            border = self.border_color

        img = self._get_bg_img(w, h, fill, border)
        self.create_image(0, 0, anchor="nw", image=img)
        self.create_text(w // 2, h // 2, text=self.text, fill=tcol, font=self.font)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpotDL Companion")
        self.geometry("900x680")
        self.minsize(780, 560)
        self.configure(bg=BG)

        self.cfg = core.load_config()
        self.ytdlp_cmd = core.find_ytdlp(self.cfg.get("spotdl_path", ""))
        self.spotdl_exe = core.find_spotdl(self.cfg.get("spotdl_path", ""))
        self.q = queue.Queue()
        self.holder = {}
        self.busy = False
        self.live_song = None       # Latest live detection dict
        self.download_queue = []    # Queue of song jobs
        self.active_job = None      # Current running job
        self.t0 = None
        self.TIMEOUT = 90           # Per-job timeout in seconds
        self.listener_proc = None
        self.link_busy = False      # Separate flag for link downloads
        self.playlist_dl = None     # Background PlaylistDownloader instance

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
        s.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("Card.TFrame", background=CARD)

        s.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        s.configure("Song.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 16, "bold"))
        s.configure("Artist.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 11))

        # Notebook & tabs
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=[20, 8],
                    font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", CARD), ("active", "#22193A")],
              foreground=[("selected", TEXT), ("active", TEXT)])

        # Entry
        s.configure("TEntry", fieldbackground=PANEL, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=6)

        # Progressbar
        s.configure("Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
        s.configure("Purple.Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

        # Scrollbar
        s.configure("TScrollbar", background=BORDER, troughcolor=PANEL, bordercolor=PANEL, arrowcolor=MUTED)
        s.map("TScrollbar", background=[("active", ACCENT), ("disabled", PANEL)])

        # Treeview
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    borderwidth=0, rowheight=28, font=("Segoe UI", 9))
        s.configure("Treeview.Heading", background=CARD, foreground=TEXT,
                    font=("Segoe UI", 9, "bold"), relief="flat", padding=6)
        s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#FFFFFF")])
        s.map("Treeview.Heading", background=[("active", "#261D42")])

        # Checkbutton
        s.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 9))
        s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])

    def _card(self, parent, title):
        outer = ttk.Frame(parent, style="Card.TFrame", padding=14)
        outer.pack(fill="x", pady=6)
        if title:
            ttk.Label(outer, text=title, style="Muted.TLabel").pack(anchor="w")
        return outer

    def _build(self):
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        header = ttk.Frame(container)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="SpotDL Companion", style="Title.TLabel").pack(side="left")
        downloader_name = os.path.basename(self.ytdlp_cmd[0]) if self.ytdlp_cmd else "None"
        ffmpeg = core.find_ffmpeg()
        ffmpeg_str = "+ FFmpeg" if ffmpeg else "(no FFmpeg)"
        self.engine_var = tk.StringVar(value=f"{downloader_name} {ffmpeg_str}")
        ttk.Label(header, textvariable=self.engine_var, style="PanelMuted.TLabel").pack(side="right", pady=(4, 0))

        # ── Persistent Bottom Bar (Always visible across both tabs) ──────────
        self.bottom_bar = ttk.Frame(container, style="Panel.TFrame", padding=10)
        self.bottom_bar.pack(side="bottom", fill="x", pady=(8, 0))

        # Bottom Row 1: Save to: <folder> [Browse] [Open folder]
        save_row = ttk.Frame(self.bottom_bar, style="Panel.TFrame")
        save_row.pack(fill="x", pady=(0, 6))
        ttk.Label(save_row, text="Save to:", style="Panel.TLabel").pack(side="left")
        self.folder_var = tk.StringVar(value=self.cfg["download_folder"])
        ttk.Entry(save_row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=8)
        RoundedButton(save_row, text="Browse...", width=86, height=28, radius=6, style="secondary",
                      parent_bg=PANEL, command=self.browse_folder).pack(side="left")
        RoundedButton(save_row, text="Open Folder", width=95, height=28, radius=6, style="secondary",
                      parent_bg=PANEL, command=self.open_folder).pack(side="left", padx=(6, 0))

        # Bottom Row 2: Status line, Cancel button, and Show Details toggle
        status_row = ttk.Frame(self.bottom_bar, style="Panel.TFrame")
        status_row.pack(fill="x", pady=(0, 4))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status_row, textvariable=self.status_var, style="Panel.TLabel").pack(side="left", fill="x", expand=True)

        self.cancel_btn = RoundedButton(status_row, text="Cancel", width=75, height=28, radius=6,
                                        style="secondary", parent_bg=PANEL, command=self.cancel)
        self.cancel_btn.configure(state="disabled")
        self.cancel_btn.pack(side="right", padx=(6, 0))

        self.details_btn = RoundedButton(status_row, text="Show details", width=96, height=28, radius=6,
                                         style="outline", parent_bg=PANEL, command=self.toggle_details)
        self.details_btn.pack(side="right")

        # Bottom Row 3: Progress bar
        self.bar = ttk.Progressbar(self.bottom_bar, style="Purple.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(4, 0))

        # Bottom Row 4: Log Area (Hidden by default, toggled via Show details)
        self.log_container = ttk.Frame(self.bottom_bar, style="Panel.TFrame")
        self.log_details_visible = False
        self.log = tk.Text(self.log_container, height=6, bg="#0B0712", fg=TEXT, insertbackground="#fff",
                           font=("Consolas", 9), wrap="word", state="disabled", relief="flat")
        sb = ttk.Scrollbar(self.log_container, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # ── Two Tabs Notebook ────────────────────────────────────────────────
        self.notebook = ttk.Notebook(container)
        self.tab_now_playing = ttk.Frame(self.notebook, style="TFrame")
        self.tab_link = ttk.Frame(self.notebook, style="TFrame")
        self.notebook.add(self.tab_now_playing, text="  Now Playing  ")
        self.notebook.add(self.tab_link, text="  Link / Playlist  ")
        self.notebook.pack(fill="both", expand=True, pady=(2, 0))

        # Tab 1: NOW PLAYING TAB CONTENT — two columns
        self.np_container = ttk.Frame(self.tab_now_playing, style="TFrame")
        self.np_container.pack(fill="both", expand=True, pady=6)

        # ── LEFT COLUMN: cover + song info + download button ──────────────────
        left_col = ttk.Frame(self.np_container, style="TFrame")
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 4))

        np_card = ttk.Frame(left_col, style="TFrame")
        np_card.place(relx=0.5, rely=0.48, anchor="center")

        self.placeholder_img = self._make_placeholder_cover(280)
        self.current_img = self.placeholder_img
        self._last_thumb = "__placeholder__"
        self._last_np_key = None

        self.thumb_label = tk.Label(np_card, image=self.placeholder_img, bg=BG, bd=0, highlightthickness=0)
        self.thumb_label.pack(pady=(0, 14))

        self.song_var = tk.StringVar(value="Waiting for Spotify...")
        self.artist_var = tk.StringVar(value="Start playing a song in Spotify.")

        self.np_title_lbl = tk.Label(np_card, textvariable=self.song_var, font=("Segoe UI", 20, "bold"),
                                     fg=TEXT, bg=BG, justify="center", wraplength=380)
        self.np_title_lbl.pack(pady=(0, 4))

        self.np_artist_lbl = tk.Label(np_card, textvariable=self.artist_var, font=("Segoe UI", 12),
                                      fg=MUTED, bg=BG, justify="center", wraplength=380)
        self.np_artist_lbl.pack(pady=(0, 18))

        self.dl_btn = RoundedButton(np_card, text="⬇   Download", width=280, height=44, radius=10,
                                    style="primary", parent_bg=BG, font=("Segoe UI", 12, "bold"),
                                    command=self.on_download_click)
        self.dl_btn.pack(pady=(0, 4))

        # ── RIGHT COLUMN: download queue panel ───────────────────────────────
        right_col = ttk.Frame(self.np_container, style="Card.TFrame", width=280)
        right_col.pack(side="right", fill="y", padx=(4, 0))
        right_col.pack_propagate(False)

        queue_header = ttk.Frame(right_col, style="Card.TFrame")
        queue_header.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(queue_header, text="DOWNLOAD QUEUE", style="Muted.TLabel").pack(side="left")
        self.queue_count_var = tk.StringVar(value="")
        ttk.Label(queue_header, textvariable=self.queue_count_var, style="Muted.TLabel").pack(side="right")

        # Separator line
        sep = tk.Frame(right_col, bg=BORDER, height=1)
        sep.pack(fill="x", padx=12, pady=(0, 6))

        # Scrollable queue list area
        queue_frame = ttk.Frame(right_col, style="Card.TFrame")
        queue_frame.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        queue_scroll = ttk.Scrollbar(queue_frame, orient="vertical")
        queue_scroll.pack(side="right", fill="y")

        self.queue_canvas = tk.Canvas(queue_frame, bg=CARD, highlightthickness=0,
                                      yscrollcommand=queue_scroll.set)
        self.queue_canvas.pack(side="left", fill="both", expand=True)
        queue_scroll.config(command=self.queue_canvas.yview)

        self.queue_inner = tk.Frame(self.queue_canvas, bg=CARD)
        self._queue_window = self.queue_canvas.create_window((0, 0), window=self.queue_inner, anchor="nw")

        self.queue_inner.bind("<Configure>", lambda e: (
            self.queue_canvas.configure(scrollregion=self.queue_canvas.bbox("all")),
            self.queue_canvas.itemconfig(self._queue_window, width=self.queue_canvas.winfo_width())
        ))
        self.queue_canvas.bind("<Configure>", lambda e: (
            self.queue_canvas.itemconfig(self._queue_window, width=e.width)
        ))
        self.queue_canvas.bind("<MouseWheel>", lambda e: self.queue_canvas.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))

        # Empty state label (shown when queue is empty)
        self.queue_empty_lbl = tk.Label(self.queue_inner, text="No songs in queue",
                                        font=("Segoe UI", 9), fg=MUTED, bg=CARD)
        self.queue_empty_lbl.pack(pady=20)

        # Tab 2: LINK / PLAYLIST TAB CONTENT
        c3 = self._card(self.tab_link, "DOWNLOAD BY LINK  (track · album · playlist)")
        link_row = ttk.Frame(c3, style="Card.TFrame")
        link_row.pack(fill="x", pady=(4, 0))
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(link_row, textvariable=self.link_var, font=("Segoe UI", 10))
        self.link_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.link_entry.insert(0, "Paste Spotify link here…")
        self.link_entry.bind("<FocusIn>", self._link_entry_focus_in)
        self.link_entry.bind("<FocusOut>", self._link_entry_focus_out)
        self.link_dl_btn = RoundedButton(link_row, text="⬇  Download", width=120, height=34, radius=6,
                                         style="primary", parent_bg=CARD, command=self.on_link_download_click)
        self.link_dl_btn.pack(side="left")

        self.link_cancel_btn = RoundedButton(link_row, text="Cancel", width=80, height=34, radius=6,
                                             style="secondary", parent_bg=CARD, command=self.on_link_cancel_click)
        self.link_cancel_btn.configure(state="disabled")
        self.link_cancel_btn.pack(side="left", padx=(6, 0))
        self.link_status_var = tk.StringVar(value="")
        ttk.Label(c3, textvariable=self.link_status_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        self.log_msg("Ready. Fast downloads via YouTube Music (yt-dlp).")

    def toggle_details(self):
        self.log_details_visible = not self.log_details_visible
        if self.log_details_visible:
            self.log_container.pack(fill="x", pady=(8, 0))
            self.details_btn.set_text("Hide details")
        else:
            self.log_container.pack_forget()
            self.details_btn.set_text("Show details")
    # ---------- Helpers ----------
    def log_msg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_queue_view(self):
        """Rebuild the queue panel to reflect current active_job + download_queue."""
        for w in self.queue_inner.winfo_children():
            w.destroy()

        items = []
        if self.active_job:
            items.append(("active", self.active_job))
        for job in self.download_queue:
            items.append(("waiting", job))

        if not items:
            lbl = tk.Label(self.queue_inner, text="No songs in queue",
                           font=("Segoe UI", 9), fg=MUTED, bg=CARD)
            lbl.pack(pady=20)
            self.queue_count_var.set("")
            return

        count = len(items)
        self.queue_count_var.set(f"{count} song{'s' if count != 1 else ''}")

        for idx, (kind, job) in enumerate(items):
            row = tk.Frame(self.queue_inner, bg=PANEL if kind == "active" else CARD)
            row.pack(fill="x", padx=4, pady=(0, 2))

            # Status icon / number
            if kind == "active":
                badge_txt = "⬇"
                badge_fg = ACCENT
            else:
                badge_txt = str(idx)  # waiting: show position number (1-based for queue items)
                badge_fg = MUTED

            badge = tk.Label(row, text=badge_txt, font=("Segoe UI", 10, "bold"),
                             fg=badge_fg, bg=PANEL if kind == "active" else CARD, width=3)
            badge.pack(side="left", padx=(6, 4), pady=6)

            info = tk.Frame(row, bg=PANEL if kind == "active" else CARD)
            info.pack(side="left", fill="x", expand=True, pady=4)

            title_fg = TEXT if kind == "active" else MUTED
            tk.Label(info, text=job["title"], font=("Segoe UI", 9, "bold" if kind == "active" else "normal"),
                     fg=title_fg, bg=PANEL if kind == "active" else CARD,
                     anchor="w", wraplength=190).pack(fill="x")
            tk.Label(info, text=job["artist"], font=("Segoe UI", 8),
                     fg=MUTED, bg=PANEL if kind == "active" else CARD,
                     anchor="w").pack(fill="x")

            if kind == "active":
                status_lbl = tk.Label(row, text="Downloading…", font=("Segoe UI", 8),
                                      fg=ACCENT, bg=PANEL)
                status_lbl.pack(side="right", padx=(4, 8))

        # Scroll to top
        self.queue_canvas.yview_moveto(0)

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
        if self.playlist_dl:
            self.playlist_dl.stop()
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

    def _link_entry_focus_in(self, _e):
        if self.link_entry.get() == "Paste Spotify link here…":
            self.link_entry.delete(0, "end")

    def _link_entry_focus_out(self, _e):
        if not self.link_entry.get().strip():
            self.link_entry.delete(0, "end")
            self.link_entry.insert(0, "Paste Spotify link here…")

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
            "thumbnail": song.get("thumbnail", ""),
            "folder": folder,
            "key": key,
        }
        self.download_queue.append(job)
        self._refresh_queue_view()

        if not self.busy:
            self._process_next_job()
        else:
            q_len = len(self.download_queue)
            self.status_var.set(f"Queued: {artist} - {title} ({q_len} waiting)")
            self.log_msg(f"Queued: {artist} - {title} ({q_len} in queue)")

    def on_link_download_click(self):
        url = self.link_var.get().strip()
        if url == "Paste Spotify link here…" or not url:
            messagebox.showwarning("No Link", "Please paste a Spotify track, album, or playlist link first.")
            return
        kind, sid = core.parse_spotify_url(url)
        if not kind:
            messagebox.showerror("Invalid Link", "That doesn't look like a Spotify track/album/playlist URL.\n\nExpected: https://open.spotify.com/...")
            return
        folder = self._precheck()
        if not folder:
            return
        if self.link_busy:
            messagebox.showinfo("Busy", "A link download is already in progress. Wait for it to finish.")
            return
        self._start_link_job(url, kind, folder)

    def _start_link_job(self, url, kind, folder):
        self.link_busy = True
        self.link_dl_btn.configure(state="disabled")
        self.link_cancel_btn.configure(state="normal")
        kind_label = {"track": "Track", "album": "Album", "playlist": "Playlist"}.get(kind, kind.capitalize())
        self.link_status_var.set(f"Starting {kind_label} download…")
        self.log_msg(f"--- Link download: {kind_label} — {url}")

        self.playlist_dl = PlaylistDownloader(self.ytdlp_cmd, self.spotdl_exe, self.q)
        self.playlist_dl.start(url, folder)


    def on_link_cancel_click(self):
        if self.playlist_dl:
            self.playlist_dl.stop()
            self.log_msg("Link download cancel requested…")
            self.link_status_var.set("Cancelling download…")
        self.link_cancel_btn.configure(state="disabled")

    def _process_next_job(self):
        if self.busy:
            return
        if not self.download_queue:
            self.active_job = None
            self.cancel_btn.configure(state="disabled")
            self.bar.stop()
            self.bar.configure(mode="determinate", value=0)
            self._refresh_queue_view()
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
        self._refresh_queue_view()

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

    # ---------- Cover Art Helpers ----------
    def _make_placeholder_cover(self, size=280):
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([(1, 1), (size - 2, size - 2)], radius=16, fill=CARD, outline=BORDER, width=2)
        try:
            font_note = ImageFont.truetype("seguisym.ttf", 64)
        except Exception:
            font_note = ImageFont.load_default()
        d.text((size // 2, size // 2 - 25), "♫", fill=ACCENT, font=font_note, anchor="mm")
        try:
            font_txt = ImageFont.truetype("segoeui.ttf", 11)
        except Exception:
            font_txt = ImageFont.load_default()
        d.text((size // 2, size // 2 + 35), "Play a song in Spotify to begin", fill=MUTED, font=font_txt, anchor="mm")
        return ImageTk.PhotoImage(im)

    def _process_cover_art(self, thumb_path, size=280):
        try:
            im = Image.open(thumb_path).convert("RGBA")
            im = im.resize((size, size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            d = ImageDraw.Draw(mask)
            d.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=16, fill=255)

            rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            rounded.paste(im, (0, 0), mask=mask)

            border_im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            db = ImageDraw.Draw(border_im)
            db.rounded_rectangle([(1, 1), (size - 2, size - 2)], radius=16, outline=ACCENT, width=2)
            out = Image.alpha_composite(rounded, border_im)
            return ImageTk.PhotoImage(out)
        except Exception:
            return None

    # ---------- Event Loop & Handlers ----------
    def _on_now_playing(self, res):
        self.live_song = res
        if res.get("ok"):
            title = res.get("title", "").strip()
            artist = res.get("artist", "").strip()
            status = res.get("status", "Playing")
            dur = res.get("duration", 0)
            dur_str = f" • {dur // 60}:{dur % 60:02d}" if dur else ""
            thumb = res.get("thumbnail", "")

            # Anti-flicker: only update if state changed
            state_key = (title, artist, status, dur, thumb)
            if getattr(self, "_last_np_key", None) == state_key:
                return
            self._last_np_key = state_key

            self.song_var.set(title)
            self.artist_var.set(f"{artist}{dur_str}  [{status}]")

            if thumb and os.path.isfile(thumb):
                if getattr(self, "_last_thumb", None) != thumb:
                    self._last_thumb = thumb
                    photo = self._process_cover_art(thumb, 280)
                    if photo:
                        self.current_img = photo
                        self.thumb_label.configure(image=photo)
            else:
                if self._last_thumb != "__placeholder__":
                    self._last_thumb = "__placeholder__"
                    self.current_img = self.placeholder_img
                    self.thumb_label.configure(image=self.placeholder_img)
        else:
            state_key = ("__none__", res.get("message", ""))
            if getattr(self, "_last_np_key", None) == state_key:
                return
            self._last_np_key = state_key

            self.song_var.set("No song playing")
            self.artist_var.set(res.get("message") or "Play a song in Spotify to begin.")
            if self._last_thumb != "__placeholder__":
                self._last_thumb = "__placeholder__"
                self.current_img = self.placeholder_img
                self.thumb_label.configure(image=self.placeholder_img)

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
            file_path = core.find_audio_file(job["folder"], job["artist"], job["title"])
            if (rc in (0, 101)) and file_path:
                thumb = job.get("thumbnail", "")
                if thumb and os.path.isfile(thumb):
                    if core.embed_spotify_cover(file_path, thumb):
                        self.log_msg(f"Cover art: Embedded exact Spotify thumbnail ({os.path.basename(thumb)})")
                msg = f"Finished in {elapsed}s: {job['artist']} - {job['title']}"
                self.status_var.set(msg)
                self.log_msg(f"SUCCESS ({elapsed}s): {job['artist']} - {job['title']}")
                self.bar.stop()
                self.bar.configure(mode="determinate", value=100)
            elif not file_path:
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
        self.active_job = None
        self._refresh_queue_view()
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
                elif kind == "link_progress":
                    status, done_n, total, label = val
                    self.link_status_var.set(label)
                elif kind == "link_line":
                    self.log_msg(val)
                elif kind == "job_done":
                    job, rc, text = val
                    self._on_job_done(job, rc, text)
                elif kind == "job_fail":
                    job, err = val
                    self.log_msg(f"Download failed to start for {job['title']}: {err}")
                    self.status_var.set(f"Error: {err}")
                    self.busy = False
                    self._process_next_job()
                elif kind == "link_job_done":
                    url, lkind, rc, summary = val
                    self.link_busy = False
                    self.link_dl_btn.configure(state="normal")
                    self.link_cancel_btn.configure(state="disabled")
                    kind_label = {"track": "Track", "album": "Album", "playlist": "Playlist"}.get(lkind, lkind.capitalize())
                    if rc == 0:
                        self.link_status_var.set(f"{kind_label} done: {summary} ✓")
                        self.log_msg(f"SUCCESS: {kind_label} — {summary} — {url}")
                    else:
                        self.link_status_var.set(f"{kind_label} complete: {summary}")
                        self.log_msg(f"Completed with notes: {kind_label} — {summary} — {url}")
                elif kind == "link_job_fail":
                    url, err = val
                    self.link_busy = False
                    self.link_dl_btn.configure(state="normal")
                    self.link_cancel_btn.configure(state="disabled")
                    self.link_status_var.set(f"Error: {err[:80]}")
                    self.log_msg(f"Link download error: {err}")
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
