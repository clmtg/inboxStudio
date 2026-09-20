"""Rule editor protected by TinyAuth at the Traefik reverse proxy."""
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import rules
import accounts

STATIC = Path(__file__).with_name('static')
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, content_type='application/json; charset=utf-8', extra=None):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def account_id(self):
        values = parse_qs(urlsplit(self.path).query).get('account', ['default'])
        key = values[0]
        accounts.get(key)
        return key

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/api/accounts':
                return self.respond(200, {'accounts':[accounts.public(a) for a in accounts.read()['accounts']]})
            if path == '/api/rules':
                return self.respond(200, rules.read(self.account_id()))
            if path == '/api/status':
                status_path = rules.directory(self.account_id()) / 'status.json'
                status = json.loads(status_path.read_text()) if status_path.exists() else {'state': 'waiting', 'message': 'Waiting for the first scan', 'results': [], 'folders': []}
                return self.respond(200, status)
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in files:
                name, mime = files[path]
                return self.respond(200, (STATIC / name).read_bytes(), mime)
            self.respond(404, {'error': 'Not found'})
        except (OSError, ValueError):
            self.respond(500, {'error': 'Could not read configuration or worker status'})

    def do_POST(self):
        # JSON plus same-origin checks prevent cross-site authenticated writes.
        origin = self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
            return self.respond(403, {'error': 'Cross-origin writes are not allowed'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.respond(415, {'error': 'Send application/json'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 262144:
                return self.respond(413, {'error': 'Request is too large or empty'})
            data = json.loads(self.rfile.read(size))
            path = urlsplit(self.path).path
            if path == '/api/accounts/test':
                return self.respond(200, accounts.test_connection(data))
            with LOCK:
                if path == '/api/accounts':
                    return self.respond(200, accounts.save(data))
                account_id = self.account_id()
                folder = rules.directory(account_id)
                if path == '/api/rules':
                    rules.validate(data)
                    previous = rules.read(account_id)
                    if data['revision'] != previous['revision']:
                        return self.respond(409, {'error': 'Rules changed in another window. Reload before saving.'})
                    data['revision'] += 1
                    rules.atomic_json(folder / 'rules.previous.json', previous)
                    rules.atomic_json(folder / 'rules.json', data)
                    return self.respond(200, data)
                if path == '/api/scan':
                    if not accounts.get(account_id)['enabled']:
                        return self.respond(400, {'error':'Enable this account before scanning'})
                    token = str(uuid.uuid4())
                    rules.atomic_json(folder / 'scan-request.json', {'id': token, 'requested_at': time.time()})
                    return self.respond(202, {'id': token, 'message': 'Scan queued'})
            self.respond(404, {'error': 'Not found'})
        except accounts.Conflict as error:
            self.respond(409, {'error': str(error)})
        except (ValueError, TypeError, KeyError) as error:
            self.respond(400, {'error': str(error)})
        except OSError:
            self.respond(500, {'error': 'Could not save to the shared data volume'})

    def log_message(self, format, *args):
        # Never log credentials or request bodies.
        print('%s %s' % (self.address_string(), format % args), flush=True)


if __name__ == '__main__':
    rules.initialize()
    accounts.initialize()
    server = ThreadingHTTPServer((os.environ.get('WEB_LISTEN', '0.0.0.0'), int(os.environ.get('PORT', '8080'))), Handler)
    server.daemon_threads = True
    print('Rule editor listening on port %d' % server.server_port, flush=True)
    server.serve_forever()
