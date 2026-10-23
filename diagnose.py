"""Diagnostics: run via 'Run Diagnostics.bat'. Writes diagnostics_report.txt next to this file."""
import os, sys, platform
import companion_core as core

lines = []
def out(s=""):
    print(s); lines.append(s)

out("SpotDL Companion diagnostics")
out("Python: " + sys.version.split()[0] + " | " + platform.platform())
try:
    import tkinter; out("Tkinter: OK")
except Exception as e:
    out("Tkinter: MISSING (" + str(e) + ") -> reinstall Python and tick 'tcl/tk and IDLE'")
cfg = core.load_config()
out("Config file: " + core.config_path())
exe = core.find_spotdl(cfg.get("spotdl_path", ""))
if exe:
    out("spotDL found: " + exe)
    out("spotDL version: " + (core.spotdl_version(exe) or "could not read version"))
else:
    out("spotDL: NOT FOUND")
ytdlp = core.find_ytdlp(cfg.get("spotdl_path", ""))
out("yt-dlp engine: " + (" ".join(ytdlp) if ytdlp else "NOT FOUND"))
ffmpeg = core.find_ffmpeg()
out("FFmpeg: " + (ffmpeg if ffmpeg else "NOT FOUND"))
spotify = os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe")
out("Spotify.exe at default path: " + ("yes" if os.path.isfile(spotify) else "not at default path (not required)"))
out("Helper script present: " + str(os.path.isfile(core.PS_SCRIPT)))
out("")
out("Media detection test (play a song in Spotify first):")
r = core.get_current_song()
if r.get("ok"):
    out(f"  OK: {r['artist']} - {r['title']} [{r['status']}]")
else:
    out("  Not detected: " + r.get("message", ""))
try:
    with open(os.path.join(core.HERE, "diagnostics_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    out("\nReport saved to diagnostics_report.txt")
except OSError:
    pass
