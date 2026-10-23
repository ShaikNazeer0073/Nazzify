SPOTDL COMPANION  (for Windows 10/11)
======================================

WHAT IT DOES
Shows the song currently playing in the Spotify desktop app and downloads it with
your existing spotDL. You can also paste a Spotify track / album / playlist link.

HOW TO RUN (3 steps)
1. Right-click the ZIP -> "Extract All..." (do NOT run from inside the ZIP).
2. Open the extracted folder and double-click "Run SpotDL Companion.bat".
3. In Spotify play a song -> click "Detect & Download".

Nothing needs installing. It uses your Python, spotDL and Spotify.
The app finds spotdl.exe automatically (e.g. %APPDATA%\Python\Python314\Scripts\spotdl.exe).
If not, click "Browse..." next to "spotDL:" and choose spotdl.exe.

FILES
 Run SpotDL Companion.bat  - double-click launcher
 spotdl_companion.py       - the app window
 companion_core.py         - logic (config, spotDL calls, song detection)
 get_media_info.ps1        - reads Windows media session (read-only)
 diagnose.py / Run Diagnostics.bat - checks Python, Tkinter, spotDL, song detection
 requirements.txt          - empty on purpose (no extra packages)

SETTINGS
Saved in %APPDATA%\SpotDLCompanion\config.json (download folder and spotDL path only).
No passwords or cookies are stored.

SPEED
Default format is M4A (no slow conversion). Switch to MP3 in Settings if your player needs it.
A timer shows elapsed time; downloads are stopped after 3 minutes.

HOW SONG DETECTION WORKS - AND LIMITS
It asks Windows' System Media Transport Controls (the same thing behind the volume
pop-up) what Spotify is playing. No screen scraping.
 * Only Spotify sessions are used; other apps (browser, VLC) are ignored.
 * If Spotify is paused, the song is still shown but labelled "Paused".
 * Ads are rejected, so you never download "Advertisement".
 * Windows only gives title and artist (no Spotify track ID), so the download uses a
   search for "Artist - Title". spotDL may pick a different version (live, remaster).
   For exact matches, paste the track's Spotify link instead.
 * Needs Windows PowerShell 5.1 (included with Windows 10/11).

IMPORTANT NOTES
 * Spotify is NOT the audio source and no DRM is bypassed. spotDL uses Spotify only
   for metadata and fetches audio from other sources (e.g. YouTube Music).
 * Use only for recordings you are authorized to download.
 * This app does not touch your Spotify or SpotX installation.

TROUBLESHOOTING
 "Python was not found"      Install Python from python.org, tick "Add to PATH".
 Window doesn't open         Double-click "Run Diagnostics.bat" and read the output.
 "spotDL not found"          Use Browse... next to "spotDL:".
 "Spotify is not reporting"  Press play in Spotify; make sure it's the desktop app.
 FFmpeg error                Command Prompt: spotdl --download-ffmpeg
 Network / rate-limit error  Wait a few minutes and retry.
 Songs saved where?          Click "Open Folder" (default: Music\SpotDL Companion).
