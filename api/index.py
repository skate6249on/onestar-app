"""Vercel Python serverless entry: wraps server.H."""
import os, sys
from urllib.parse import urlsplit, parse_qsl, urlencode
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

_ready = False
def _init_once():
    global _ready
    if not _ready:
        server.init()  # CREATE TABLE IF NOT EXISTS -> idempotent
        _ready = True

class handler(server.H):
    def _restore_path(self):
        # vercel.json rewrites /(.*) -> /api/index?__p=/$1 ; restore original path.
        u = urlsplit(self.path)
        q = parse_qsl(u.query, keep_blank_values=True)
        orig = [v for k, v in q if k == "__p"]
        if orig:
            rest = urlencode([(k, v) for k, v in q if k != "__p"])
            self.path = (orig[0] or "/") + ("?" + rest if rest else "")
        elif u.path in ("/api/index", "/api/index.py"):
            fwd = self.headers.get("x-vercel-original-path") or self.headers.get("x-forwarded-uri")
            self.path = fwd or "/"
    def do_GET(self):
        self._restore_path(); _init_once(); return super().do_GET()
    def do_POST(self):
        self._restore_path(); _init_once(); return super().do_POST()
