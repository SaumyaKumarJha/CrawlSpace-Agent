"""Tiny server for the fixture site: adds a redirect and a 404 so the crawler
sees the response shapes it will meet in the wild."""
import http.server, functools, sys, os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/old.html"):
            self.send_response(301); self.send_header("Location", "/about.html")
            self.end_headers(); return
        super().do_GET()
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    handler = functools.partial(H, directory=ROOT)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
