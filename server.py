from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import json, os, re, shutil, subprocess, uuid, urllib.parse, threading, time

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
UPLOADS = ROOT / "uploads"
OUTPUT = ROOT / "output"
UPLOADS.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)
MAX_SIZE = 2 * 1024 * 1024 * 1024
STALE_AFTER = 2 * 60 * 60
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
SESSION_LOCK = threading.Lock()

def get_ffmpeg():
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg is not installed. Run run.bat again or install imageio-ffmpeg."
        ) from exc

def probe(path):
    ff = get_ffmpeg()
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
    s = (r.stderr or "") + "\n" + (r.stdout or "")
    dm = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", s)
    vm = re.search(r"Stream #\d+(?::\d+)?[^\n]*?Video:[^\n]*?(\d{2,5})x(\d{2,5})", s)
    duration = 0.0
    if dm:
        h, m, sec = dm.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(sec)
    width = height = 0
    if vm:
        width, height = map(int, vm.groups())
    if not duration and not width:
        raise RuntimeError((r.stderr or "Could not read the video.")[-2000:])
    return {"duration": duration, "width": width, "height": height}

def valid_session(value):
    return bool(SESSION_RE.fullmatch(str(value or "")))

def safe_filename(value):
    name = Path(str(value or "CinderClip")).stem
    name = re.sub(r"[^A-Za-z0-9 _.-]+", "", name).strip(" .")
    return name[:120] or "CinderClip"

def session_prefix(session_id):
    if not valid_session(session_id):
        raise ValueError("Invalid session.")
    return session_id + "_"

def cleanup_session(session_id):
    prefix = session_prefix(session_id)
    removed = 0
    with SESSION_LOCK:
        for root in (UPLOADS, OUTPUT):
            for item in root.iterdir():
                if item.is_file() and item.name.startswith(prefix):
                    try:
                        item.unlink()
                        removed += 1
                    except OSError:
                        pass
    return removed

def cleanup_all_temp():
    removed = 0
    with SESSION_LOCK:
        for root in (UPLOADS, OUTPUT):
            for item in root.iterdir():
                if item.is_file():
                    try:
                        item.unlink()
                        removed += 1
                    except OSError:
                        pass
    return removed

def cleanup_stale():
    cutoff = time.time() - STALE_AFTER
    with SESSION_LOCK:
        for root in (UPLOADS, OUTPUT):
            for item in root.iterdir():
                if not item.is_file():
                    continue
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                except OSError:
                    pass

def start_session(session_id):
    if not valid_session(session_id):
        raise ValueError("Invalid session.")
    # CinderClip's current local engine intentionally keeps one active browser session.
    # Starting a fresh page therefore clears files from previous sessions immediately.
    with SESSION_LOCK:
        for root in (UPLOADS, OUTPUT):
            for item in root.iterdir():
                if item.is_file() and not item.name.startswith(session_prefix(session_id)):
                    try:
                        item.unlink()
                    except OSError:
                        pass
    cleanup_stale()

def save_multipart(handler, session_id):
    length = int(handler.headers.get("Content-Length", "0"))
    if length > MAX_SIZE:
        raise ValueError("File too large")
    ctype = handler.headers.get("Content-Type", "")
    m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
    if not m:
        raise ValueError("Invalid multipart boundary")
    boundary = (m.group(1) or m.group(2)).encode()
    body = handler.rfile.read(length)
    for part in body.split(b"--" + boundary):
        if b"Content-Disposition:" not in part:
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        data = data.rstrip(b"\r\n-")
        fm = re.search(br'filename="([^"]+)"', head)
        if not fm:
            continue
        original = fm.group(1).decode("utf-8", "ignore")
        ext = Path(original).suffix.lower()
        if ext not in {".mp4",".mov",".mkv",".webm",".m4v",".avi"}:
            raise ValueError("Unsupported video format")
        source_stem = safe_filename(original)
        name = f"{session_prefix(session_id)}{uuid.uuid4().hex}__{source_stem}{ext}"
        path = UPLOADS / name
        path.write_bytes(data)
        return name, original, path
    raise ValueError("No video file found")

def max_dims(meta, aspect):
    sw, sh = meta["width"], meta["height"]
    if aspect == "9:16":
        h = (int(min(sh, sw * 16 / 9)) // 2) * 2
        w = (int(round(h * 9 / 16)) // 2) * 2
        return w, h, h
    if aspect == "16:9":
        w = (int(min(sw, sh * 16 / 9)) // 2) * 2
        h = (int(round(w * 9 / 16)) // 2) * 2
        return w, h, w
    side = (min(sw, sh) // 2) * 2
    return side, side, side

class Handler(BaseHTTPRequestHandler):
    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Access-Control-Request-Private-Network, Access-Control-Request-Local-Network")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Allow-Local-Network", "true")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Max-Age", "86400")

    def send_json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        session_id = query.get("session", [""])[0]
        if path == "/api/health":
            try:
                ff = get_ffmpeg()
                return self.send_json({"ok": True, "version": "0.1.0", "ffmpeg": os.path.basename(ff), "runtime": "local-processor", "storage": "local-disk"})
            except Exception as exc:
                return self.send_json({"ok": False, "version": "0.1.0", "error": str(exc)}, 503)
        if path.startswith("/download/"):
            name = Path(path).name
            if not valid_session(session_id) or not name.startswith(session_prefix(session_id)):
                return self.send_json({"error": "Invalid session or file."}, 400)
            p = OUTPUT / name
            if not p.exists():
                return self.send_error(404)
            data = p.read_bytes()
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/octet-stream")
            clean_download_name = name.split("__", 1)[1] if "__" in name else name
            self.send_header("Content-Disposition", f'attachment; filename="{clean_download_name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path.startswith("/media/"):
            name = Path(path).name
            p = OUTPUT / name
            if not p.exists():
                return self.send_error(404)
            data = p.read_bytes()
            range_header = self.headers.get("Range")
            start, end = 0, len(data) - 1
            if range_header and range_header.startswith("bytes="):
                try:
                    spec = range_header[6:].split(",", 1)[0]
                    a, b = spec.split("-", 1)
                    if a:
                        start = int(a)
                    if b:
                        end = int(b)
                    else:
                        end = min(start + 1024 * 1024 - 1, len(data) - 1)
                    start = max(0, min(start, len(data) - 1))
                    end = max(start, min(end, len(data) - 1))
                    chunk = data[start:end + 1]
                    self.send_response(206)
                    self.send_cors()
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
                except Exception:
                    pass
            self.send_response(200)
            self.send_cors()
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        target = PUBLIC / ("index.html" if path in ("", "/") else Path(path).name)
        if not target.exists():
            return self.send_error(404)
        data = target.read_bytes()
        ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "image/x-icon" if target.suffix == ".ico" else "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            session_id = query.get("session", [""])[0]

            if path == "/api/session/start":
                start_session(session_id)
                return self.send_json({"ok": True, "session": session_id, "storage": "temporary-session"})

            if path == "/api/session/cleanup":
                removed = cleanup_session(session_id)
                return self.send_json({"ok": True, "session": session_id, "removed": removed})

            if path == "/api/upload":
                start_session(session_id)
                name, original, path = save_multipart(self, session_id)
                meta = probe(path)
                return self.send_json({"id": name, "name": original, **meta})
            if path == "/api/generate":
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                vid = Path(str(body.get("id", ""))).name
                if not vid.startswith(session_prefix(session_id)) or not valid_session(session_id):
                    return self.send_json({"error": "Invalid session or video."}, 400)
                src = UPLOADS / vid
                if not src.exists():
                    return self.send_json({"error": "Video not found."}, 404)
                ff = get_ffmpeg()
                meta = probe(src)
                count = max(1, min(int(body.get("count", 5)), 10))
                clip = min(max(int(body.get("length", 35)), 1), 90, max(meta["duration"], 1))
                aspect = str(body.get("aspect", "9:16"))
                quality = str(body.get("quality", "balanced")).lower()
                mw, mh, ml = max_dims(meta, aspect)
                req = str(body.get("resolution", "source")).lower().strip()
                if req in {"", "source", "max", "source max"}:
                    target = ml
                elif req.startswith("custom:"):
                    target = int(req.split(":", 1)[1])
                else:
                    target = int(req.rstrip("p")) 
                target = max(2, min(target, ml))
                target -= target % 2
                if aspect == "9:16":
                    out_h, out_w = target, max(2, (round(target * 9 / 16) // 2) * 2)
                elif aspect == "16:9":
                    out_w, out_h = target, max(2, (round(target * 9 / 16) // 2) * 2)
                else:
                    out_w = out_h = target
                want = 9/16 if aspect == "9:16" else 16/9 if aspect == "16:9" else 1
                ratio = meta["width"] / meta["height"] if meta["height"] else 1
                if ratio > want:
                    crop_h = meta["height"]; crop_w = int(round(crop_h * want))
                else:
                    crop_w = meta["width"]; crop_h = int(round(crop_w / want))
                crop_w = max(2, crop_w // 2 * 2); crop_h = max(2, crop_h // 2 * 2)
                cx = max((meta["width"] - crop_w) // 2, 0); cy = max((meta["height"] - crop_h) // 2, 0)
                preset, crf = ("superfast", "18") if quality == "high" else ("ultrafast", "20")
                vf = f"crop={crop_w}:{crop_h}:{cx}:{cy},scale={out_w}:{out_h}:flags=bicubic,setsar=1"
                maxstart = max(meta["duration"] - clip, 0)
                clips = []
                for i in range(count):
                    start = maxstart / 2 if count == 1 else maxstart * i / (count - 1)
                    source_stem = src.name.split("__", 1)[1] if "__" in src.name else "CinderClip"
                    source_stem = Path(source_stem).stem
                    out = f"{session_prefix(session_id)}{uuid.uuid4().hex}__{source_stem} - Clip-{i+1}.mp4"
                    dest = OUTPUT / out
                    r = subprocess.run(
                        [ff, "-y", "-ss", str(start), "-i", str(src), "-t", str(clip),
                         "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
                         "-c:v", "libx264", "-preset", preset, "-crf", crf,
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                         "-movflags", "+faststart", str(dest)],
                        capture_output=True, text=True, errors="replace"
                    )
                    if r.returncode:
                        raise RuntimeError((r.stderr or "FFmpeg failed.")[-1500:])
                    clean_title = f"{source_stem} - Clip-{i+1}"
                    clips.append({"id": out, "title": clean_title, "download_name": clean_title + ".mp4", "start": start, "end": start+clip,
                                  "duration": clip, "width": out_w, "height": out_h,
                                  "resolution": f"{out_w}×{out_h}", "url": "/media/" + urllib.parse.quote(out)})
                return self.send_json({"clips": clips, "mode": "candidate-windows",
                                       "source": {"width": meta["width"], "height": meta["height"]},
                                       "max_output": {"width": mw, "height": mh, "long_side": ml}})
            return self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, 500)

    def log_message(self, *args):
        pass

# Remove leftovers from previous app runs before accepting a fresh session.
cleanup_all_temp()

def stale_cleanup_loop():
    while True:
        try:
            cleanup_stale()
        except Exception:
            pass
        time.sleep(300)

threading.Thread(target=stale_cleanup_loop, daemon=True).start()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
print(f"CinderClip server -> http://{HOST}:{PORT}")
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
