# CinderClip

A lightweight local video-to-short clip MVP with a fiery CinderClip UI.

## Current build
- Black/obsidian background with fiery orange/red/amber accents
- Drag-and-drop video upload
- Local FFmpeg processing
- 9:16, 1:1, or 16:9 output
- Resolution selector from 480p through 2160p where the source supports it
- Custom long-side resolution
- Source maximum option that never upscales beyond the dropped video
- Balanced and High quality export modes
- Browser preview + MP4 download
- Legal pages: privacy, terms, disclaimer

## Run on Windows
1. Install Python 3.10+.
2. Double-click `run.bat`.
3. The launcher automatically installs `imageio-ffmpeg` if needed.
4. Open http://localhost:8787

## Note
This experimental build generates evenly-spaced clip candidates. The AI moment-selection engine will later replace these candidates.

The legal pages are general experimental-product drafts and should be reviewed before a public commercial launch or hosted deployment.


## Current Cloudflare architecture

The hosted Worker serves the CinderClip interface, while the current video engine remains local. When the hosted site is opened, the browser looks for the local engine at `http://127.0.0.1:8787`. When the local engine is running, uploads, FFmpeg processing, previews, and downloads are sent directly to that local service.

This keeps the current MVP usable without adding paid cloud storage. Video files are temporary session files rather than a permanent library. Starting a new session clears previous session files, page exit/refresh attempts cleanup, and a stale-file cleanup pass handles interrupted sessions. Cloud storage and hosted video processing can be plugged in later without replacing the UI.

### Hosted UI + local engine

1. Run `run.bat` on the computer that will process videos.
2. Open the hosted CinderClip Worker URL.
3. The status pill should change to **Local Engine · Connected**.
4. Upload and process normally.

The local processor remains bound to `127.0.0.1:8787`. CORS is enabled only as a browser bridge; the service is not exposed on the LAN.
