"""Serialize scans, load one immutable rules snapshot, publish status."""
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

import rules

stopping = False
child = None


def stop(*_):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()


def request_id():
    try:
        return json.loads((rules.DATA / 'scan-request.json').read_text())['id']
    except (OSError, ValueError, KeyError):
        return None


def scan(document):
    global child
    snapshot = '/tmp/active-rules.lua'
    rules.render(document, snapshot)
    started = time.time()
    status = {'state': 'running', 'started_at': started, 'revision': document['revision'],
              'preview': document['settings']['preview'], 'results': [], 'folders': [], 'logs': [], 'heartbeat_at': started}
    status_path = rules.DATA / 'status.json'
    rules.atomic_json(status_path, status)
    child = subprocess.Popen(['imapfilter', '-c', os.environ.get('FILTER_CONFIG', '/config/config.lua')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env={**os.environ, 'RULES_FILE': snapshot}, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    buffer = b''
    last_write = started
    timed_out = False

    def line_received(raw):
        line = raw.decode('utf-8', errors='replace').rstrip('\r')
        if line.startswith('FOLDER\t'):
            status['folders'].append(line.split('\t', 1)[1])
            return  # Internal folder metadata for the UI, not a log entry.
        if line.startswith('RULE_RESULT\t'):
            _, key, count = line.split('\t')
            status['results'].append({'id': key, 'count': int(count)})
            return  # The following readable summary is the public log entry.
        print(line, flush=True)
        if line.startswith('INBOX_COUNT\t'):
            status['inbox_count'] = int(line.split('\t')[1])
        else:
            status['logs'] = (status['logs'] + [line])[-80:]

    try:
        while selector.get_map():
            now = time.time()
            if stopping or now - started > 3600:
                timed_out = not stopping
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                break
            for key, _ in selector.select(timeout=1):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    line_received(line)
            if now - last_write >= 2:
                status['heartbeat_at'] = now
                rules.atomic_json(status_path, status)
                last_write = now
        if buffer:
            line_received(buffer)
        code = child.wait()
    finally:
        selector.close()
        child.stdout.close()
        if child.poll() is None:
            child.kill()
            child.wait()
    status.update(state='success' if code == 0 and not stopping and not timed_out else 'error',
                  finished_at=time.time(), heartbeat_at=time.time(), exit_code=code)
    if timed_out:
        status['logs'].append('Scan stopped after one hour; review mailbox size and interval.')
    status['next_scan_at'] = time.time() + document['settings']['interval_seconds']
    rules.atomic_json(status_path, status)


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Web service initializes the shared rules file; worker never overwrites it.
    while not stopping and not (rules.DATA / 'rules.json').exists():
        time.sleep(1)
    while not stopping:
        consumed = request_id()
        try:
            document = rules.read()
            scan(document)
        except Exception as error:
            print('Scan failed: ' + str(error), flush=True)
            rules.atomic_json(rules.DATA / 'status.json', {'state': 'error', 'finished_at': time.time(),
                'logs': ['Scan failed: ' + str(error)], 'results': [], 'folders': []})
        finished = time.time()
        while not stopping:
            try:
                interval = rules.read()['settings']['interval_seconds']
            except Exception:
                interval = 300
            if request_id() != consumed or time.time() - finished >= interval:
                break
            time.sleep(1)


if __name__ == '__main__':
    main()
