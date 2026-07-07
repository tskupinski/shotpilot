"""Read-only web UI (`shot ui`): selects gallery, cuts, inputs — on localhost.

One page (webui.html) fed by /api/status (the `shot status` payload plus URLs
and exists-flags), video streamed straight from disk with HTTP Range from two
whitelisted roots (output/ and the input dir), poster thumbnails (selects
and inputs) generated on demand into output/ui-cache/thumbs/ (mtime cache,
like motion.csv). Strictly read-only by construction: GET/HEAD only and no
manifest writer is ever imported here.
"""

import datetime
import email.utils
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from . import config, ffmpeg, manifest, paths, sequence, status

THUMB_WIDTH = 480
CHUNK = 1024 * 1024
MIME = {".mp4": "video/mp4", ".mov": "video/mp4", ".m4v": "video/mp4",
        ".mts": "video/mp2t", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".txt": "text/plain; charset=utf-8", ".csv": "text/csv",
        ".mp3": "audio/mpeg", ".wav": "audio/wav"}

# first gallery load requests dozens of thumbnails at once — do not launch
# that many concurrent 4K decodes
_THUMB_SEM = threading.BoundedSemaphore(2)


# ------------------------------------------------------------- pure helpers

def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single-range `Range` header -> inclusive (start, end).

    None = serve the full 200 response (no/malformed/multi-range header —
    RFC 7233 allows ignoring it); ValueError = 416 (start beyond the file).
    """
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    start_s, sep, end_s = header[6:].partition("-")
    if not sep:
        return None
    try:
        if start_s == "":
            n = int(end_s)  # suffix: last n bytes
            if n <= 0:
                return None
            return max(size - n, 0), size - 1
        start, end = int(start_s), (int(end_s) if end_s else size - 1)
    except ValueError:
        return None
    if start >= size:
        raise ValueError(f"range start {start} beyond size {size}")
    if start > end:
        return None
    return start, min(end, size - 1)


def safe_path(rel: str, root: Path) -> Path | None:
    """URL remainder -> existing file inside `root`, or None.

    resolve() neutralizes both `..` and symlink escapes in one step.
    """
    rel = urllib.parse.unquote(rel)
    if not rel or rel.startswith("/") or "\x00" in rel:
        return None
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        return None
    return p if p.is_file() else None


def media_url(path_str: str) -> str | None:
    """Manifest path -> /media/... URL (only under output/ or the input dir)."""
    p = Path(path_str)
    for root, prefix in ((paths.OUTPUT, "/media/output/"),
                         (config.input_dir(), "/media/input/")):
        try:
            return prefix + urllib.parse.quote(str(p.relative_to(root)))
        except ValueError:
            continue
    return None


# ------------------------------------------------------------- API payload

def build_ui_payload() -> dict:
    """`shot status` payload + URLs, exists-flags and per-cut sequence rows."""
    data = manifest.load()
    payload = status.build_status(data)
    by_source: dict[str, list[str]] = {}
    for s in payload["selects"]:
        p, src = Path(s["file"]), Path(s["source"])
        rng = s.get("range")
        s.update(url=media_url(s["file"]), exists=p.is_file(),
                 thumb=f"/thumb/{urllib.parse.quote(p.stem)}.jpg",
                 duration_s=round(rng[1] - rng[0], 2) if rng else None,
                 source_url=media_url(s["source"]), source_exists=src.is_file(),
                 source_stem=src.stem,
                 speed_variant_urls={k: media_url(v) for k, v
                                     in s.get("speed_variants", {}).items()})
        by_source.setdefault(s["source"], []).append(s["file"])
    for i in payload["inputs"]:
        contact = paths.video_dir(i["stem"]) / "contact.png"
        i["contact_url"] = media_url(str(contact)) if contact.is_file() else None
        i["selects"] = by_source.get(i["file"], [])
        i["url"] = media_url(i["file"])
        i["exists"] = Path(i["file"]).is_file()
        i["thumb"] = f"/thumb/input/{urllib.parse.quote(i['stem'])}.jpg"
    for name, c in payload["cuts"].items():
        cut = manifest.get_cut(data, name)
        rows = sequence.sequence_view(cut, payload["selects"])[0]["sequence"]
        for r in rows:
            r["use_url"] = media_url(r["use"])
            r["use_exists"] = Path(r["use"]).is_file()
        out = (cut.get("render") or {}).get("out")
        applied = c["music"]["applied"]
        c.update(sequence=rows,
                 render_url=media_url(out) if out else None,
                 render_exists=bool(out) and Path(out).is_file(),
                 final_url=media_url(applied["out"]) if applied.get("out") else None)
    payload["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return payload


# -------------------------------------------------------------- thumbnails

def _poster(src: Path, at: float, thumb: Path) -> Path | None:
    """Cached-or-generated poster frame of `src` at `at` seconds, None if the
    source file is missing. Atomic tmp+replace — concurrent racers are safe."""
    if not src.is_file():
        return None
    if thumb.is_file() and thumb.stat().st_mtime >= src.stat().st_mtime:
        return thumb
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f"{thumb.stem}.{threading.get_ident()}.part.jpg")
    with _THUMB_SEM:
        ffmpeg.extract_frame(src, at, tmp, width=THUMB_WIDTH)
    os.replace(tmp, thumb)
    return thumb


def thumb_for(entry: dict) -> Path | None:
    """Poster of a select: midpoint frame of the cut."""
    src = Path(entry["file"])
    rng = entry.get("range") or (0, 0)
    return _poster(src, max((rng[1] - rng[0]) / 2, 0.0),
                   paths.UI_CACHE / "thumbs" / f"{src.stem}.jpg")


def input_thumb(stem: str) -> Path | None:
    """Poster of an input source: midpoint frame (1 s in when unanalyzed).

    Separate cache subdir — an input stem may equal a select stem."""
    input_dir = config.input_dir()
    src = next((f for f in sorted(input_dir.iterdir())
                if f.stem == stem and f.suffix.lower() in status.VIDEO_EXT),
               None) if input_dir.exists() else None
    if src is None:
        return None
    summary = paths.video_dir(stem) / "summary.json"
    at = 1.0
    if summary.is_file():
        at = json.loads(summary.read_text())["video"]["duration"] / 2
    return _poster(src, at, paths.UI_CACHE / "thumbs" / "input" / f"{stem}.jpg")


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "shotpilot-ui"

    def handle(self):
        # covers the whole keep-alive loop, incl. reading the NEXT request
        # line after the browser resets the connection on a video seek —
        # that happens outside the per-request guard in _handle()
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):  # noqa: N802 — http.server API
        self._handle(head=False)

    def do_HEAD(self):  # noqa: N802
        self._handle(head=True)

    def _deny(self):
        self.send_error(405, "read-only server (GET/HEAD only)")

    do_POST = do_PUT = do_DELETE = do_PATCH = _deny  # noqa: N815

    def _handle(self, head: bool) -> None:
        try:
            self._route(head)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browsers abort connections on every video seek
        except Exception as e:  # noqa: BLE001 — one request must not kill the thread
            try:
                self.send_error(500, str(e))
            except OSError:
                pass

    def _route(self, head: bool) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            page = (resources.files("pipeline") / "webui.html").read_bytes()
            self._send_bytes(page, MIME[".html"], head, "no-cache")
        elif path == "/api/status":
            body = json.dumps(build_ui_payload(), ensure_ascii=False).encode()
            self._send_bytes(body, MIME[".json"], head, "no-store")
        elif path.startswith("/thumb/input/"):
            stem = urllib.parse.unquote(
                path.removeprefix("/thumb/input/")).removesuffix(".jpg")
            self._file(input_thumb(stem), head)
        elif path.startswith("/thumb/"):
            self._thumb(path.removeprefix("/thumb/"), head)
        elif path.startswith("/media/output/"):
            self._file(safe_path(path.removeprefix("/media/output/"),
                                 paths.OUTPUT), head)
        elif path.startswith("/media/input/"):
            self._file(safe_path(path.removeprefix("/media/input/"),
                                 config.input_dir()), head)
        else:
            self.send_error(404)

    def _thumb(self, name: str, head: bool) -> None:
        stem = urllib.parse.unquote(name).removesuffix(".jpg")
        entry = next((s for s in manifest.load()["selects"]
                      if Path(s["file"]).stem == stem), None)
        self._file(thumb_for(entry) if entry else None, head)

    # -------------------------------------------------------- file serving

    def _send_bytes(self, body: bytes, ctype: str, head: bool,
                    cache: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _file(self, path: Path | None, head: bool) -> None:
        if path is None:
            self.send_error(404)
            return
        st = path.stat()
        mtime = int(st.st_mtime)
        ims = self.headers.get("If-Modified-Since")
        if ims:
            try:
                since = email.utils.parsedate_to_datetime(ims).timestamp()
            except (TypeError, ValueError):
                since = -1
            if mtime <= since:
                self.send_response(304)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        try:
            rng = parse_range(self.headers.get("Range"), st.st_size)
        except ValueError:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{st.st_size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng if rng else (0, st.st_size - 1)
        length = end - start + 1 if st.st_size else 0
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{st.st_size}")
        self.send_header("Content-Type",
                         MIME.get(path.suffix.lower(),
                                  "application/octet-stream"))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified",
                         email.utils.formatdate(mtime, usegmt=True))
        self.end_headers()
        if head:
            return
        with path.open("rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def log_request(self, code="-", size="-"):
        try:
            ok = int(str(code)) < 400
        except ValueError:
            ok = False
        if not ok:  # logs on stderr, successes quiet (CLI convention)
            self.log_message('"%s" %s', self.requestline, str(code))


def serve(port: int = 8765, open_browser: bool = True,
          as_json: bool = False) -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"cannot bind 127.0.0.1:{port} ({e.strerror}) — try --port N",
              file=sys.stderr)
        return 1
    actual = server.server_address[1]
    url = f"http://127.0.0.1:{actual}/"
    if as_json:  # emitted BEFORE blocking — a backgrounded agent parses the port
        print(json.dumps({"url": url, "port": actual, "pid": os.getpid()}),
              flush=True)
    print(f"UI: {url} (Ctrl-C stops)", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    finally:
        server.server_close()
    return 0
