"""Validated rule storage and data-only Lua export. No user-supplied Lua."""
import json
import os
import re
import tempfile
from pathlib import Path

DATA = Path(os.environ.get('DATA_DIR', '/data'))
DEFAULTS = Path(__file__).with_name('defaults.json')
FIELDS = {'sender_domain', 'sender_email', 'subject', 'body', 'subject_or_body', 'age_hours', 'flagged'}


def validate(document):
    if not isinstance(document, dict) or document.get('schema') != 1:
        raise ValueError('Unsupported rules format')
    if type(document.get('revision')) is not int or document['revision'] < 0:
        raise ValueError('Invalid revision')
    settings = document.get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError('Invalid settings')
    if type(settings.get('preview')) is not bool:
        raise ValueError('Choose preview or live mode')
    interval = settings.get('interval_seconds')
    if type(interval) is not int or not 60 <= interval <= 86400:
        raise ValueError('Scan interval must be between 60 and 86400 seconds')
    rules = document.get('rules')
    if not isinstance(rules, list) or len(rules) > 200:
        raise ValueError('Use at most 200 rules')
    ids = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError('Invalid rule')
        key = rule.get('id', '')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', key) or key in ids:
            raise ValueError('Rule IDs must be unique')
        ids.add(key)
        clean_text(rule.get('name'), 'Rule name', 120)
        if type(rule.get('enabled')) is not bool:
            raise ValueError('Invalid enabled setting')
        validate_conditions(rule.get('conditions'))
        validate_action(rule, conditional=True)
    # Reject unknown, non-JSON objects and excessive nesting before persistence.
    json.dumps(document, allow_nan=False)
    return document


def validate_action(action, conditional=False):
    if not isinstance(action, dict):
        raise ValueError('Invalid action')
    kind = action.get('action')
    if conditional and kind == 'conditional':
        branches = action.get('branches')
        if not isinstance(branches, list) or not 1 <= len(branches) <= 10:
            raise ValueError('Conditional rules need 1–10 branches')
        for branch in branches:
            if not isinstance(branch, dict):
                raise ValueError('Invalid branch')
            validate_conditions(branch.get('conditions'))
            validate_action(branch)
        validate_action(action.get('otherwise'))
    elif kind not in {'move', 'keep', 'delete', 'continue'}:
        raise ValueError('Choose move, keep, delete, or continue')
    elif kind == 'move':
        clean_text(action.get('folder'), 'Destination folder', 255)
        if action['folder'].upper() == 'INBOX':
            raise ValueError('Use keep in Inbox instead of moving to INBOX')


def validate_conditions(conditions):
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 10:
        raise ValueError('Each rule needs 1–10 conditions')
    for condition in conditions:
        if not isinstance(condition, dict) or condition.get('field') not in FIELDS:
            raise ValueError('Unknown condition field')
        field, op, value = condition['field'], condition.get('op'), condition.get('value')
        if field == 'flagged':
            if op != 'is' or value is not True:
                raise ValueError('Flag status must be is flagged')
        elif field == 'age_hours':
            if op not in {'older_than', 'at_most'} or type(value) not in {int, float} or not 0 < value <= 87600:
                raise ValueError('Age must be greater than 0 and at most 87600 hours')
        else:
            clean_text(value, 'Condition value', 300)
            if field == 'sender_domain':
                if op != 'is' or not re.fullmatch(r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}', value):
                    raise ValueError('Enter a sender domain such as amazon.fr, without @')
            elif field == 'sender_email':
                if op not in {'is', 'contains'}:
                    raise ValueError('Sender email supports is or contains')
                if op == 'is' and not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
                    raise ValueError('Enter a complete sender email address')
            elif op not in {'contains', 'not_contains'}:
                raise ValueError('Invalid text comparison')

def clean_text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError(f'{label} must be nonempty, at most {limit} characters, without control characters')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / 'rules.json'
    if not path.exists():
        document = json.loads(DEFAULTS.read_text())
        mode = os.environ.get('DRY_RUN', 'true')
        if mode not in {'true', 'false'}:
            raise ValueError('DRY_RUN must be true or false')
        document['settings']['preview'] = mode == 'true'
        document['settings']['interval_seconds'] = int(os.environ.get('INTERVAL_SECONDS', '300'))
        atomic_json(path, validate(document))


def read():
    return validate(json.loads((DATA / 'rules.json').read_text()))


def lua(value):
    """Serialize only data; decimal byte escapes make arbitrary strings inert."""
    if isinstance(value, str):
        return '"' + ''.join('\\%03d' % byte for byte in value.encode('utf-8')) + '"'
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) in {int, float}:
        return str(value)
    if isinstance(value, list):
        return '{' + ','.join(lua(item) for item in value) + '}'
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua(key) + ']=' + lua(item) for key, item in value.items()) + '}'
    if value is None:
        return 'nil'
    raise ValueError('Unsupported configuration value')


def render(document, path):
    validate(document)
    Path(path).write_text('return ' + lua(document) + '\n')
