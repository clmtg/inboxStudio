"""Serialize scans, load one immutable rules snapshot, publish status."""
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path

import rules
import accounts

stopping = False
child = None


def stop(*_):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()


def request_id(account_id='default'):
    try:
        return json.loads((rules.directory(account_id) / 'scan-request.json').read_text())['id']
    except (OSError, ValueError, KeyError):
        return None


def scan(document, account=None):
    global child
    account_id = account['id'] if account else 'default'
    snapshot = '/tmp/active-rules.lua'
    rules.render(document, snapshot)
    started = time.time()
    status = {'state': 'running', 'started_at': started, 'revision': document['revision'],
              'preview': document['settings']['preview'], 'results': [], 'folders': [], 'logs': [], 'heartbeat_at': started}
    status_path = rules.directory(account_id) / 'status.json'
    environment = {**os.environ, 'RULES_FILE': snapshot}
    if account:
        environment.update(IMAP_HOST=account['host'], IMAP_PORT=str(account['port']),
                           IMAP_USERNAME=account['username'], IMAP_PASSWORD=account['password'])
    rules.atomic_json(status_path, status)
    child = subprocess.Popen(['imapfilter', '-c', os.environ.get('FILTER_CONFIG', '/config/config.lua')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=environment, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    buffer = b''
    last_write = started
    timed_out = False

    def line_received(raw):
        line = raw.decode('utf-8', errors='replace').rstrip('\r')
        secret = environment.get('IMAP_PASSWORD')
        if secret:
            line = line.replace(secret, '[redacted]')
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


def run_pending(schedule):
    """Process due accounts serially; one account failure does not block others."""
    for listed in accounts.read()['accounts']:
        if stopping:
            return
        account = accounts.get(listed['id'])
        key = account['id']
        if not account['enabled']:
            schedule.pop(key, None)
            continue
        entry = schedule.get(key)
        requested = request_id(key)
        try:
            document = rules.read(key)
            if entry and requested == entry['request'] and time.time() - entry['finished'] < document['settings']['interval_seconds']:
                continue
            scan(document, account)
        except Exception:
            # Do not expose server responses or credential-bearing exceptions.
            print('Scan failed for account ' + key, flush=True)
            rules.atomic_json(rules.directory(key) / 'status.json', {'state':'error',
                'finished_at':time.time(), 'logs':['Scan failed. Check account settings and connection.'],
                'results':[], 'folders':[]})
        schedule[key] = {'request':requested, 'finished':time.time()}


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    accounts.initialize(import_legacy=True)
    schedule = {}
    while not stopping:
        try:
            run_pending(schedule)
        except (OSError, ValueError, KeyError):
            print('Could not read account configuration; retrying.', flush=True)
        time.sleep(1)


if __name__ == '__main__':
    main()
