import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import accounts
import rules
import worker
import test_app


def account_input(name='Personal'):
    return {'name':name,'host':'imap.example.com','port':993,'username':'mail@example.com',
            'password':'test-only-password','enabled':True}


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(rules, 'DATA', Path(self.temp.name))
        self.patcher.start()
        accounts.initialize()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_password_write_only_and_preserved_on_edit(self):
        result = accounts.save(account_input())
        self.assertNotIn('password', result)
        self.assertTrue(result['password_set'])
        self.assertEqual((rules.DATA / 'accounts.json').stat().st_mode & 0o777, 0o600)
        result['name'] = 'Renamed'
        accounts.save(result)
        self.assertEqual(accounts.get(result['id'])['password'], 'test-only-password')
        with self.assertRaises(accounts.Conflict): accounts.save(result)

    def test_cannot_forward_saved_password_to_changed_server(self):
        result = accounts.save(account_input())
        for field, value in [('host','other.example.com'),('port',1993),('username','other@example.com')]:
            changed = {**result, field:value}
            with self.assertRaises(ValueError): accounts.save(changed)
            with self.assertRaises(ValueError): accounts.test_connection(changed)
        accounts.save({**result,'host':'other.example.com','password':'replacement'})
        self.assertEqual(accounts.get(result['id'])['password'], 'replacement')

    def test_legacy_import_once_and_preserves_rules(self):
        rules.initialize()
        original = rules.read()
        original['revision'] = 17
        rules.atomic_json(rules.DATA / 'rules.json', original)
        with patch.dict(os.environ, {'IMAP_USERNAME':'legacy@example.com','IMAP_PASSWORD':'legacy-password'}):
            accounts.initialize(import_legacy=True)
            legacy = accounts.public(accounts.get('default'))
            accounts.save({**legacy,'password':'updated'})
            accounts.initialize(import_legacy=True)
        self.assertEqual(accounts.get('default')['password'], 'updated')
        self.assertEqual(rules.read()['revision'], 17)
        self.assertEqual(len(accounts.read()['accounts']), 1)

    def test_accounts_start_with_independent_preview_rules(self):
        a = accounts.save(account_input('A')); b = accounts.save(account_input('B'))
        da = rules.read(a['id']); da['settings']['preview'] = False
        rules.atomic_json(rules.directory(a['id']) / 'rules.json', da)
        self.assertTrue(rules.read(b['id'])['settings']['preview'])
        self.assertEqual(rules.read(b['id'])['rules'], [])
        with self.assertRaises(ValueError): accounts.get('../accounts.json')

    def test_connection_readonly_and_errors_do_not_leak(self):
        with patch.object(accounts.imaplib, 'IMAP4_SSL') as ssl:
            client = ssl.return_value
            client.select.return_value = ('OK', [b'5'])
            self.assertTrue(accounts.test_connection(account_input())['ok'])
            client.select.assert_called_once_with('INBOX', readonly=True)
            client.logout.assert_called_once()
            context = ssl.call_args.kwargs['ssl_context']
            self.assertTrue(context.check_hostname)
            client.login.side_effect = accounts.imaplib.IMAP4.error('test-only-password')
            result = accounts.test_connection(account_input())
            self.assertFalse(result['ok'])
            self.assertNotIn('test-only-password', json.dumps(result))

    def test_scheduler_isolated_requests_and_paused_accounts(self):
        a = accounts.save(account_input('A')); b = accounts.save(account_input('B'))
        schedule = {}
        with patch.object(worker, 'scan') as scan:
            worker.run_pending(schedule)
            self.assertEqual(scan.call_count, 2)
            worker.run_pending(schedule)
            self.assertEqual(scan.call_count, 2)
            rules.atomic_json(rules.directory(b['id']) / 'scan-request.json', {'id':'requested'})
            worker.run_pending(schedule)
            self.assertEqual(scan.call_count, 3)
            self.assertEqual(scan.call_args.args[1]['id'], b['id'])
            accounts.save({**a,'enabled':False})
            rules.atomic_json(rules.directory(a['id']) / 'scan-request.json', {'id':'paused'})
            worker.run_pending(schedule)
            self.assertEqual(scan.call_count, 3)

    def test_worker_uses_only_selected_credentials_and_redacts_logs(self):
        a = accounts.save(account_input('A'))
        b = accounts.save({**account_input('B'),'host':'second.example.com','username':'second@example.com','password':'second-secret'})
        script = Path(self.temp.name) / 'fake.py'
        script.write_text("import os\nprint(os.environ['IMAP_PASSWORD'])\nprint('Connected')\n")
        real_popen = subprocess.Popen
        seen = []
        def spawn(_command, **kwargs):
            seen.append(kwargs['env'])
            return real_popen([sys.executable, str(script)], **kwargs)
        with patch.object(worker.subprocess, 'Popen', spawn):
            for item in (a,b): worker.scan(rules.read(item['id']), accounts.get(item['id']))
        self.assertEqual([e['IMAP_USERNAME'] for e in seen], ['mail@example.com','second@example.com'])
        self.assertEqual([e['IMAP_HOST'] for e in seen], ['imap.example.com','second.example.com'])
        for item in (a,b):
            status = (rules.directory(item['id']) / 'status.json').read_text()
            self.assertNotIn('test-only-password', status)
            self.assertNotIn('second-secret', status)
            self.assertIn('[redacted]', status)

    def test_failure_of_one_account_does_not_stop_another(self):
        a = accounts.save(account_input('A')); b = accounts.save(account_input('B'))
        with patch.object(worker, 'scan', side_effect=[RuntimeError('secret'),None]) as scan:
            worker.run_pending({})
            self.assertEqual(scan.call_count, 2)
        status = json.loads((rules.directory(a['id']) / 'status.json').read_text())
        self.assertNotIn('secret', json.dumps(status))


class AccountHTTPTests(unittest.TestCase):
    setUpClass = classmethod(test_app.HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(test_app.HTTPTests.tearDownClass.__func__)
    request = test_app.HTTPTests.request

    def test_account_api_and_rule_isolation(self):
        code, body, _ = self.request('/api/accounts', account_input('API account'))
        self.assertEqual(code, 200)
        a = json.loads(body)
        self.assertNotIn('password', a)
        listing = self.request('/api/accounts')[1]
        self.assertNotIn(b'test-only-password', listing)
        url = '/api/rules?account=' + a['id']
        doc = json.loads(self.request(url)[1]); doc['settings']['interval_seconds'] = 600
        self.assertEqual(self.request(url, doc)[0], 200)
        self.assertEqual(json.loads(self.request(url)[1])['settings']['interval_seconds'], 600)
        self.assertNotEqual(json.loads(self.request('/api/rules')[1])['settings']['interval_seconds'], 600)
        self.assertEqual(self.request('/api/scan?account='+a['id'], {})[0], 202)
        self.assertTrue((rules.directory(a['id']) / 'scan-request.json').exists())
        self.assertEqual(self.request('/api/accounts', {**a,'enabled':False})[0], 200)
        self.assertEqual(self.request('/api/scan?account='+a['id'], {})[0], 400)
        self.assertEqual(self.request('/api/accounts', {**a,'enabled':True})[0], 409)
        self.assertEqual(self.request('/api/accounts', account_input(), origin='https://evil.example')[0], 403)
