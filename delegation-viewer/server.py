#!/usr/bin/env python
# Delegation Viewer — a tiny live window onto every task Claude hands to a
# worker model. It tails the firehose written by delegation_log.py (the tools
# log there; this just reads). Stdlib only. Port 8809 (Echo is 8808).
import http.server
import json
import os

PORT = 8809
LOG_PATH = r"C:\Claude-LLM-Projects\local-agents\dump\delegations.jsonl"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/':
                self._send_file('index.html', 'text/html')
            elif self.path.startswith('/api/log'):
                # /api/log?offset=N -> entries from line N onward + total count.
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                offset = int(q.get('offset', ['0'])[0])
                lines = []
                if os.path.exists(LOG_PATH):
                    with open(LOG_PATH, 'r', encoding='utf-8') as f:
                        lines = f.read().splitlines()
                total = len(lines)
                entries = []
                for ln in lines[offset:]:
                    ln = ln.strip()
                    if ln:
                        try:
                            entries.append(json.loads(ln))
                        except json.JSONDecodeError:
                            pass
                self._send_json({'entries': entries, 'total': total})
            else:
                self.send_error(404, "Not Found")
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def _send_file(self, name, ctype):
        with open(os.path.join(os.path.dirname(__file__), name), 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def log_message(self, *a):
        pass  # quiet


if __name__ == '__main__':
    with http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler) as httpd:
        print(f"Delegation Viewer on http://127.0.0.1:{PORT}")
        httpd.serve_forever()
