"""Single-admin mailbox configuration. Passwords are write-only over HTTP."""
import fcntl
import imaplib
import ipaddress
import json
import os
import re
import ssl
import uuid
from contextlib import contextmanager

import rules


class Conflict(ValueError):
    pass


@contextmanager
def locked():
    rules.DATA.mkdir(parents=True, exist_ok=True)
    with (rules.DATA / '.accounts.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read():
    path = rules.DATA / 'accounts.json'
    return json.loads(path.read_text()) if path.exists() else {'accounts': [], 'legacy_imported': False}


def public(account):
    return {**{k: v for k, v in account.items() if k != 'password'},
            'password_set': bool(account.get('password'))}


def get(key):
    rules.directory(key)  # Validate before using an ID in a file path.
    for account in read()['accounts']:
        if account['id'] == key:
            return account
    raise ValueError('Account not found')


def initialize(import_legacy=False):
    with locked():
        data = read()
        if import_legacy and not data['legacy_imported']:
            username, password = os.environ.get('IMAP_USERNAME'), os.environ.get('IMAP_PASSWORD')
            if username and password and not any(a['id'] == 'default' for a in data['accounts']):
                rules.initialize()
                data['accounts'].insert(0, {'id':'default', 'revision':0, 'name':'iCloud Mail',
                    'host':'imap.mail.me.com', 'port':993, 'username':username,
                    'password':password, 'enabled':True})
            data['legacy_imported'] = True
        rules.atomic_json(rules.DATA / 'accounts.json', data)


def prepared(data, previous=None):
    if not isinstance(data, dict):
        raise ValueError('Invalid account')
    result = {}
    for field, limit in [('name', 120), ('host', 253), ('username', 254)]:
        rules.clean_text(data.get(field), field.title(), limit)
        result[field] = data[field].strip()
    host = result['host']
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host):
            raise ValueError('Enter a server hostname, without a URL or port')
    port = data.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    if type(data.get('enabled')) is not bool:
        raise ValueError('Choose whether to enable scanning')
    result.update(port=port, enabled=data['enabled'])
    password = data.get('password', '')
    if not isinstance(password, str) or len(password) > 1024 or any(ord(c) < 32 for c in password):
        raise ValueError('Invalid password')
    if not password and previous:
        if any(result[k] != previous[k] for k in ('host', 'port', 'username')):
            raise ValueError('Re-enter the password when changing the server, port, or username')
        password = previous['password']
    if not password:
        raise ValueError('Enter an app-specific password or IMAP password')
    result['password'] = password
    return result


def save(data):
    with locked():
        state = read()
        key = data.get('id') if isinstance(data, dict) else None
        previous = get(key) if key else None
        if previous and data.get('revision') != previous['revision']:
            raise Conflict('Account changed in another window. Reopen it before saving.')
        if not previous and len(state['accounts']) >= 20:
            raise ValueError('Use at most 20 accounts')
        account = prepared(data, previous)
        account.update(id=key or uuid.uuid4().hex, revision=previous['revision']+1 if previous else 0)
        if previous:
            state['accounts'] = [account if a['id'] == key else a for a in state['accounts']]
        else:
            rules.initialize(account['id'])
            state['accounts'].append(account)
        rules.atomic_json(rules.DATA / 'accounts.json', state)
        return public(account)


def test_connection(data):
    previous = get(data['id']) if isinstance(data, dict) and data.get('id') else None
    account = prepared(data, previous)
    connection = None
    try:
        connection = imaplib.IMAP4_SSL(account['host'], account['port'],
                                     ssl_context=ssl.create_default_context(), timeout=10)
        connection.login(account['username'], account['password'])
        code, _ = connection.select('INBOX', readonly=True)
        if code != 'OK':
            return {'ok':False, 'message':'Connected, but Inbox could not be opened.'}
        return {'ok':True, 'message':'Connection successful. Inbox is accessible; no messages were changed.'}
    except (OSError, imaplib.IMAP4.error):
        return {'ok':False, 'message':'Connection failed. Check the server, TLS port, username, and app-specific password.'}
    finally:
        if connection:
            try:
                connection.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
