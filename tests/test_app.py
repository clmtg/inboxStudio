import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import rules
import accounts
import server
import worker


def defaults():
    return json.loads((ROOT / 'tests/fixtures/rules.json').read_text())


def conditional_document():
    data = defaults()
    data['settings']['preview'] = False
    data['rules'] = [{
        'id': 'apple', 'name': 'Apple', 'enabled': True,
        'conditions': [{'field': 'sender_domain', 'op': 'is', 'value': 'apple.com'}],
        'action': 'conditional',
        'branches': [{'conditions': [{'field': 'age_hours', 'op': 'older_than', 'value': 24}],
                      'action': 'move', 'folder': 'Store/Amazon'}],
        'otherwise': {'action': 'keep'},
    }]
    return data


class ValidationTests(unittest.TestCase):
    def test_conditional_validation(self):
        self.assertIsNotNone(rules.validate(conditional_document()))
        for change in [lambda r: r.update(branches=[]),
                       lambda r: r.pop('otherwise'),
                       lambda r: r['otherwise'].update(action='conditional'),
                       lambda r: r['branches'][0].update(folder=''),
                       lambda r: r['branches'][0].update(conditions=[])]:
            data = conditional_document()
            change(data['rules'][0])
            with self.assertRaises(ValueError): rules.validate(data)

    def test_new_install_has_no_rules_and_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rules, 'DATA', Path(directory)):
            with patch.dict(os.environ, {'DRY_RUN':'true', 'INTERVAL_SECONDS':'300'}):
                rules.initialize()
            initial = rules.read()
            self.assertEqual(initial['rules'], [])
            self.assertTrue(initial['settings']['preview'])
            rules.atomic_json(rules.DATA / 'rules.json', defaults())
            rules.initialize()
            self.assertEqual(rules.read(), defaults())

    def test_defaults_and_disabled_rules(self):
        data = defaults()
        data['rules'][0]['enabled'] = False
        self.assertIs(rules.validate(data), data)

    def test_reject_invalid_and_unbounded_inputs(self):
        for field, value in [('action', 'execute'), ('id', 'same\nline'), ('conditions', []), ('enabled', 1)]:
            data = defaults()
            data['rules'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                rules.validate(data)
        data = defaults()
        data['rules'][0]['conditions'][0]['value'] = 'insurance.example.com;touch /tmp/no'
        with self.assertRaises(ValueError): rules.validate(data)
        data = defaults()
        data['rules'].append(copy.deepcopy(data['rules'][0]))
        with self.assertRaises(ValueError): rules.validate(data)
        data = defaults()
        data['rules'][0]['conditions'][2]['value'] = float('nan')
        with self.assertRaises(ValueError): rules.validate(data)

    def test_sender_email_validation(self):
        for op, value, valid in [('is','hello@example.com',True), ('contains','hello+',True),
                                 ('is','hello',False), ('not_contains','hello',False)]:
            data = defaults()
            data['rules'][0]['conditions'] = [{'field':'sender_email','op':op,'value':value}]
            if valid: rules.validate(data)
            else:
                with self.assertRaises(ValueError): rules.validate(data)

    def test_atomic_file_and_inert_lua_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rules.json'
            rules.atomic_json(path, defaults())
            self.assertEqual(json.loads(path.read_text()), defaults())
            self.assertFalse(list(Path(directory).glob('.write-*')))
        encoded = rules.lua('"; os.execute("oops") -- sécurité')
        self.assertNotIn('os.execute', encoded)
        self.assertTrue(encoded.startswith('"\\034'))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.old_data = rules.DATA
        rules.DATA = Path(cls.directory.name)
        rules.atomic_json(rules.DATA / 'rules.json', defaults())
        with patch.dict(os.environ, {'IMAP_USERNAME':'fixture@example.com','IMAP_PASSWORD':'fixture-password'}):
            accounts.initialize(import_legacy=True)
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.http.server_port

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        rules.DATA = cls.old_data
        cls.directory.cleanup()

    def request(self, path, data=None, origin=None):
        headers = {}
        if data is not None: headers['Content-Type'] = 'application/json'
        if origin: headers['Origin'] = origin
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as response:
            return response.code, response.read(), response.headers

    def test_proxy_auth_has_no_second_login_and_static_security(self):
        for path in ['/', '/api/rules', '/api/status']:
            code, _, headers = self.request(path)
            self.assertEqual(code, 200)
            self.assertNotIn('WWW-Authenticate', headers)
        code, body, headers = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn(b'Inbox Studio', body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../../.env')[0], 404)

    def test_save_reorder_disable_remove_add_and_conflict(self):
        data = json.loads(self.request('/api/rules')[1])
        data['rules'].reverse()
        data['rules'][0]['enabled'] = False
        data['rules'].pop()
        new = copy.deepcopy(data['rules'][0]); new['id']='new-test';new['name']='New rule';data['rules'].append(new)
        self.assertEqual(self.request('/api/rules', data)[0], 200)
        stored = json.loads(self.request('/api/rules')[1])
        self.assertEqual(stored['rules'], data['rules'])
        self.assertEqual(stored['revision'], data['revision'] + 1)
        self.assertTrue((rules.DATA / 'rules.previous.json').exists())
        self.assertEqual(self.request('/api/rules', data)[0], 409)
        self.assertEqual(self.request('/api/rules', stored, origin='https://untrusted.example')[0], 403)

    def test_save_conditional_rule_roundtrip(self):
        data = json.loads(self.request('/api/rules')[1])
        rule = conditional_document()['rules'][0]
        data['rules'].append(rule)
        self.assertEqual(self.request('/api/rules', data)[0], 200)
        stored = json.loads(self.request('/api/rules')[1])
        self.assertEqual(stored['rules'][-1], rule)

    def test_scan_queue_and_status(self):
        self.assertEqual(self.request('/api/scan', {})[0], 202)
        self.assertIn('id', json.loads((rules.DATA / 'scan-request.json').read_text()))
        self.assertEqual(json.loads(self.request('/api/status')[1])['state'], 'waiting')


class WorkerTests(unittest.TestCase):
    def run_fake_scan(self, code):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'fake_filter.py'
            script.write_text("print('FOLDER\\tStore/Amazon')\nprint('INBOX_COUNT\\t7')\nprint('RULE_RESULT\\tamazon\\t2')\nprint('Finished')\nraise SystemExit(%d)\n" % code)
            real_popen = subprocess.Popen
            def spawn(_command, **kwargs):
                return real_popen([sys.executable, str(script)], **kwargs)
            with patch.object(rules, 'DATA', Path(directory)), patch.object(worker.subprocess, 'Popen', spawn):
                worker.scan(defaults())
                return json.loads((Path(directory) / 'status.json').read_text())

    def test_scan_status_and_results(self):
        status = self.run_fake_scan(0)
        self.assertEqual(status['state'], 'success')
        self.assertEqual(status['inbox_count'], 7)
        self.assertEqual(status['results'], [{'id':'amazon','count':2}])
        self.assertEqual(status['folders'], ['Store/Amazon'])
        self.assertTrue(status['preview'])

    def test_failed_scan_is_not_reported_as_success(self):
        status = self.run_fake_scan(1)
        self.assertEqual(status['state'], 'error')
        self.assertEqual(status['exit_code'], 1)


LUA = os.environ.get('LUA_BIN') or shutil.which('lua')

@unittest.skipUnless(LUA, 'Set LUA_BIN to run Lua engine tests')
class EngineTests(unittest.TestCase):
    def test_conditional_age_boundary_keep_and_continue(self):
        data = conditional_document()
        data['rules'].append({'id':'later','name':'Later','enabled':True,'action':'delete',
                              'conditions':data['rules'][0]['conditions']})
        messages = [{'from':'<hello@apple.com>','subject':'Hello','date':date}
                    for date in [1000000-86401, 1000000-86400, 1000000-60]]
        result = self.run_engine(messages, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([s for s in result.stdout.splitlines() if s.startswith('MUTATION')],
                         ['MUTATION move 1 Store/Amazon'])
        data['rules'][0]['otherwise']['action'] = 'continue'
        result = self.run_engine(messages, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MUTATION delete 2', result.stdout)
        self.assertIn('MUTATION delete 3', result.stdout)
        data['settings']['preview'] = True
        self.assertNotIn('MUTATION', self.run_engine(messages, data).stdout)

    def test_conditional_order_unknown_date_and_missing_folder(self):
        data = conditional_document()
        data['rules'][0]['branches'].append({
            'conditions':[{'field':'age_hours','op':'older_than','value':1}], 'action':'delete'})
        mail = [{'from':'<hello@apple.com>','subject':'Hello','date':1}]
        result = self.run_engine(mail, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MUTATION move 1 Store/Amazon', result.stdout)
        self.assertNotIn('MUTATION delete', result.stdout)
        data['rules'][0]['branches'].reverse()
        self.assertIn('MUTATION delete 1', self.run_engine(mail, data).stdout)
        data['rules'][0]['otherwise']['action'] = 'delete'
        result = self.run_engine([{'from':'<hello@apple.com>','subject':'Hello'}], data)
        self.assertNotIn('MUTATION', result.stdout)
        self.assertIn('Left unchanged', result.stdout)
        data['rules'][0]['branches'].reverse()
        result = self.run_engine(mail, data, folders=[])
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('MUTATION', result.stdout)

    def test_sender_email_exact_and_literal_contains(self):
        data = conditional_document()
        rule = data['rules'][0]
        rule['conditions'] = [{'field':'sender_email','op':'is','value':'alerts+shop@apple.com'}]
        messages = [
            {'from':'From: Apple <ALERTS+SHOP@APPLE.COM>', 'date':1},
            {'from':'alerts+shop@apple.com', 'date':1},
            {'from':'"alerts+shop@apple.com" <other@example.com>', 'date':1},
            {'from':'alerts+shop@apple.com.evil.test', 'date':1},
            {'from':'other@apple.com', 'date':1},
        ]
        result = self.run_engine(messages, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith('MUTATION')],
                         ['MUTATION move 1 Store/Amazon', 'MUTATION move 2 Store/Amazon'])
        rule['conditions'] = [{'field':'sender_domain','op':'is','value':'apple.com'}]
        rule['branches'][0]['conditions'] = [{'field':'sender_email','op':'contains','value':'ALERTS+SHOP@'}]
        result = self.run_engine(messages, data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith('MUTATION')],
                         ['MUTATION move 1 Store/Amazon', 'MUTATION move 2 Store/Amazon'])


    def test_legacy_subjects_without_utf8_library(self):
        phrase = 'Votre code de sécurité'
        samples = [
            ('=?ISO-8859-1?Q?Votre_code_de_s=E9curit=E9?=', phrase),
            ('=?ISO-8859-1?B?' + base64.b64encode(phrase.encode('latin-1')).decode() + '?=', phrase),
            ('=?windows-1252?Q?Votre_code_de_s=E9curit=E9_=97_10_=80?=', phrase + ' — 10 €'),
            ('=?UTF-8?Q?Votre_code_de_s=C3=A9curit=C3=A9?=', phrase),
            ('=?ISO-8859-1?Q?Votre_code_de_?=\r\n =?ISO-8859-1?Q?s=E9curit=E9?=', phrase),
        ]
        script = "utf8=nil; local h=dofile('app/helpers.lua');\n"
        for encoded, expected in samples:
            script += 'assert(h.decoded_subject(%s) == %s)\n' % (rules.lua(encoded), rules.lua(expected))
        script += "assert(h.decoded_subject('=?windows-1252?Q?=81?=') == nil)\n"
        result = subprocess.run([LUA, '-'], input=script, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_engine(self, messages, document=None, folders=None):
        document = document or defaults()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'rules.lua'
            fixture = Path(directory) / 'fixture.lua'
            rules.render(document, config)
            fixture.write_text('return ' + rules.lua({'now': 1000000, 'messages': messages, 'folders': folders if folders is not None else ['Pro/Linkedin','Store/Amazon','Store/Leboncoin','Store/Uber','Health/Insurance']}))
            return subprocess.run([LUA, 'tests/engine_harness.lua'], cwd=ROOT, env={**os.environ, 'RULES_FILE':str(config), 'FIXTURE_FILE':str(fixture), 'IMAP_USERNAME':'test', 'IMAP_PASSWORD':'test'}, capture_output=True, text=True)

    def test_preview_never_mutates(self):
        result = self.run_engine([{'from':'From: <hello@linkedin.com>','subject':'Hi','date':1}, {'from':'From: <code@insurance.example.com>','subject':'Votre code de sécurité','date':1}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('MUTATION', result.stdout)
        self.assertIn('RULE_RESULT\tgeneration-expired\t1', result.stdout)
        self.assertIn('RULE_RESULT\tlinkedin\t1', result.stdout)

    def test_live_exact_boundary_domain_and_encoded_subject(self):
        data=defaults();data['settings']['preview']=False
        phrase='Votre code de sécurité'
        encoded='=?UTF-8?B?' + base64.b64encode(phrase.encode()).decode() + '?='
        messages=[
            {'from':'<code@insurance.example.com>','subject':encoded,'date':1000000-72*3600-1},
            {'from':'<code@insurance.example.com>','subject':phrase,'date':1000000-72*3600},
            {'from':'<code@insurance.example.com>','subject':phrase,'date':1000000-60},
            {'from':'<hello@insurance.example.com>','subject':'Votre remboursement','date':1},
            {'from':'<notify@linkedin.com.attacker.test>','subject':'Hello','date':1},
            {'from':'<notifications-noreply@linkedin.com>','subject':'Hello','date':1},
            {'from':'<other@example.org>','subject':'Votre commande Uber Eats','date':1},
            {'from':'<parcel@colisprive.com>','subject':'Livraison','body':'Votre colis AMAZON arrive','date':1},
        ]
        result=self.run_engine(messages,data)
        self.assertEqual(result.returncode,0,result.stderr)
        mutations=[line for line in result.stdout.splitlines() if line.startswith('MUTATION')]
        self.assertEqual(set(mutations), {'MUTATION delete 1','MUTATION move 4 Health/Insurance','MUTATION move 6 Pro/Linkedin','MUTATION move 7 Store/Uber','MUTATION move 8 Store/Amazon'})

    def test_keep_priority_and_disabled_rule(self):
        data=defaults();data['settings']['preview']=False
        data['rules']=[{'id':'keep','name':'Keep','enabled':True,'action':'keep','folder':'','conditions':[{'field':'subject','op':'contains','value':'important'}]}, {'id':'move','name':'Move','enabled':True,'action':'move','folder':'Store/Amazon','conditions':[{'field':'sender_domain','op':'is','value':'amazon.fr'}]}]
        mail=[{'from':'<order@amazon.fr>','subject':'important','date':1}]
        self.assertNotIn('MUTATION',self.run_engine(mail,data).stdout)

        data['rules'].reverse()
        self.assertIn('MUTATION move 1 Store/Amazon',self.run_engine(mail,data).stdout)
        data['rules'][0]['enabled']=False
        self.assertNotIn('MUTATION',self.run_engine(mail,data).stdout)

    def test_flagged_keep_protects_against_move_and_delete(self):
        data=defaults();data['settings']['preview']=False
        data['rules'].insert(0, {'id':'protect-flagged','name':'Protect flagged mail',
            'enabled':True,'action':'keep','folder':'',
            'conditions':[{'field':'flagged','op':'is','value':True}]})
        result=self.run_engine([
            {'from':'<hello@amazon.fr>','subject':'Order','date':1,'flags':['\\Flagged']},
            {'from':'<code@insurance.example.com>','subject':'Votre code de sécurité','date':1,'flags':['\\Flagged']},
            {'from':'<hello@amazon.fr>','subject':'Order','date':1,'flags':[]},
            {'from':'<code@insurance.example.com>','subject':'Votre code de sécurité','date':1,'flags':[]},
        ],data)
        self.assertEqual(result.returncode,0,result.stderr)
        mutations=[line for line in result.stdout.splitlines() if line.startswith('MUTATION')]
        self.assertEqual(set(mutations), {'MUTATION move 3 Store/Amazon','MUTATION delete 4'})
        self.assertIn('RULE_RESULT\tprotect-flagged\t2',result.stdout)

    def test_unreadable_subject_and_missing_destination_fail_closed(self):
        data=defaults();data['settings']['preview']=False
        result=self.run_engine([{'from':'<code@insurance.example.com>','subject':'=?unknown?Q?some_text?=','date':1}],data)
        self.assertNotIn('MUTATION',result.stdout)
        self.assertIn('Left unchanged',result.stdout)
        result=self.run_engine([{'from':'<a@amazon.fr>','subject':'Hello','date':1}],data,folders=[])
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('MUTATION',result.stdout)
        self.assertIn('Destination folder does not exist',result.stderr)

    def test_literal_text_not_regex(self):
        data=defaults();data['settings']['preview']=False
        data['rules']=[{'id':'literal','name':'Literal','enabled':True,'action':'move','folder':'Store/Amazon','conditions':[{'field':'subject','op':'contains','value':'Price (EUR) + [offer].'}]}]
        result=self.run_engine([{'from':'<a@example.org>','subject':'Price (EUR) + [offer]. today','date':1},{'from':'<a@example.org>','subject':'Price EUR offers','date':1}],data)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('MUTATION move 1 Store/Amazon',result.stdout)
        self.assertNotIn('MUTATION move 2',result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
