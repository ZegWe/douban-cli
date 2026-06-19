from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(frozen=True)
class RemoteControlConfig:
    host: str
    port: int


class RemoteControlServer:
    def __init__(self, config: RemoteControlConfig) -> None:
        self.config = config
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._screenshot = b""
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((config.host, config.port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def update_screenshot(self, data: bytes) -> None:
        with self._lock:
            self._screenshot = data

    def drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path in {"/", "/index.html"}:
                    self._send_html()
                    return
                if self.path.startswith("/screenshot.jpg"):
                    self._send_screenshot()
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/event":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                try:
                    data = json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_error(400)
                    return
                if isinstance(data, dict) and data.get("type") in {"move", "down", "up"}:
                    owner._events.put(data)
                self.send_response(204)
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_html(self) -> None:
                body = CONTROL_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_screenshot(self) -> None:
                with owner._lock:
                    data = owner._screenshot
                if not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


CONTROL_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Douban Login Remote Control</title>
  <style>
    html, body { margin: 0; background: #111; color: #eee; font-family: sans-serif; }
    #bar { padding: 8px 10px; font-size: 14px; background: #1d1d1d; }
    #screen { display: block; width: 100%; max-width: 1280px; margin: 0 auto; touch-action: none; user-select: none; }
  </style>
</head>
<body>
  <div id="bar">远程登录画面。直接点击或拖动验证码；登录成功后 SSH 里的脚本会自动保存 cookie。</div>
  <img id="screen" draggable="false" alt="remote browser screenshot">
  <script>
    const img = document.getElementById('screen');
    let pointerDown = false;

    function coords(ev) {
      const rect = img.getBoundingClientRect();
      const sx = img.naturalWidth / rect.width;
      const sy = img.naturalHeight / rect.height;
      return {
        x: Math.max(0, Math.round((ev.clientX - rect.left) * sx)),
        y: Math.max(0, Math.round((ev.clientY - rect.top) * sy))
      };
    }

    async function send(type, ev) {
      if (!img.naturalWidth) return;
      await fetch('/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, ...coords(ev) })
      }).catch(() => {});
    }

    img.addEventListener('pointerdown', ev => {
      pointerDown = true;
      img.setPointerCapture(ev.pointerId);
      send('down', ev);
      ev.preventDefault();
    });
    img.addEventListener('pointermove', ev => {
      if (pointerDown) send('move', ev);
      ev.preventDefault();
    });
    img.addEventListener('pointerup', ev => {
      pointerDown = false;
      send('up', ev);
      ev.preventDefault();
    });
    img.addEventListener('pointercancel', ev => {
      pointerDown = false;
      send('up', ev);
    });

    function refresh() {
      img.src = '/screenshot.jpg?t=' + Date.now();
    }
    img.addEventListener('load', () => setTimeout(refresh, 350));
    img.addEventListener('error', () => setTimeout(refresh, 1000));
    refresh();
  </script>
</body>
</html>
"""
