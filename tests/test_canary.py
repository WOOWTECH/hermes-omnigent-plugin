import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import omnigent

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / '.github' / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


canary = load('omnigent_canary', 'omnigent-canary.py')
anchors = load('check_omnigent_compat', 'check-omnigent-compat.py')

PASS = {'status': 'pass', 'step': None}
RESULTS = {'pin': '0.16.0', 'rows': [
    {'version': '0.16.0', 'contract': PASS, 'anchors': {'status': 'pass', 'code': None, 'file': None}},
    {'version': '0.19.1', 'contract': PASS, 'anchors': {'status': 'fail', 'code': 'OMNIGENT_COMPAT_ANCHOR_MISSING', 'file': 'runner/app.py'}},
]}


def release(*versions, yanked=()):
    return {'releases': {v: [{'yanked': v in yanked}] for v in versions}}


class CanaryTests(unittest.TestCase):
    def test_the_pin_is_read_from_the_installer(self):
        self.assertRegex(canary.pinned((ROOT / 'hermes_omnigent' / 'runtime.py').read_text()), r'^\d+\.\d+\.\d+$')
        with self.assertRaises(SystemExit):
            canary.pinned('nothing pinned here')
        with self.assertRaises(SystemExit):
            canary.pinned("'omnigent==0.16.0' and 'omnigent==0.17.0'")

    def test_only_stable_unyanked_releases_above_the_pin_count_newest_few_oldest_first(self):
        index = release('0.9.0', '0.16.0', '0.17.0', '0.18.0', '0.19.1', '0.20.0', '0.21.0rc1', '0.21.0', '0.22.0', '0.10.0', yanked=('0.22.0',))
        self.assertEqual(canary.newer_releases(index, '0.16.0', 10), ['0.17.0', '0.18.0', '0.19.1', '0.20.0', '0.21.0'])
        self.assertEqual(canary.newer_releases(index, '0.16.0', 2), ['0.20.0', '0.21.0'])
        # numeric, not alphabetical: 0.10.0 is above 0.9.0
        self.assertEqual(canary.newer_releases(index, '0.9.0', 20)[:2], ['0.10.0', '0.16.0'])
        self.assertEqual(canary.newer_releases(index, '0.9.0', 3), ['0.19.1', '0.20.0', '0.21.0'])
        self.assertEqual(canary.newer_releases({}, '0.16.0', 3), [])
        self.assertEqual(canary.newer_releases(index, '0.16.0', 0), [])

    def test_last_json_line_is_what_a_check_ends_with(self):
        self.assertEqual(canary.last_json('ok step\n{"ok": true}\n'), {'ok': True})
        self.assertIsNone(canary.last_json('no json here\n'))
        self.assertIsNone(canary.last_json('{broken\n'))
        self.assertIsNone(canary.last_json('[1, 2]\n'))

    def test_contract_outcomes(self):
        ok = json.dumps({'ok': True, 'steps': [{'step': 'a', 'ok': True}]})
        bad = json.dumps({'ok': False, 'steps': [{'step': 'a', 'ok': True}, {'step': 'connect: fresh login, managed host online', 'ok': False}]})
        self.assertEqual(canary.contract_result(0, f'ok   a\n{ok}\n'), {'status': 'pass', 'step': None})
        self.assertEqual(canary.contract_result(1, f'FAIL\n{bad}\n'), {'status': 'fail', 'step': 'connect: fresh login, managed host online'})
        self.assertEqual(canary.contract_result(2, 'setup failed: x\n')['status'], 'error')
        self.assertEqual(canary.contract_result(0, 'no summary\n')['status'], 'error')
        # an exit code of 0 never counts as a pass without the summary saying ok
        self.assertEqual(canary.contract_result(0, json.dumps({'ok': False, 'steps': []}))['status'], 'fail')

    def test_anchor_outcomes(self):
        self.assertEqual(canary.anchors_result(0, '{"ok": true}')['status'], 'pass')
        failed = canary.anchors_result(1, '{"ok": false, "code": "OMNIGENT_COMPAT_ANCHOR_MISSING", "file": "runner/app.py", "line": 162}')
        self.assertEqual((failed['status'], failed['code'], failed['file']), ('fail', 'OMNIGENT_COMPAT_ANCHOR_MISSING', 'runner/app.py'))
        self.assertEqual(canary.anchors_result(2, '')['status'], 'error')
        self.assertEqual(canary.anchors_result(0, 'no json')['status'], 'error')

    def test_the_table_marks_the_pin_and_names_what_failed(self):
        text = canary.table(RESULTS)
        self.assertIn('| `0.16.0` (pinned SDK) | ✅ | ✅ |', text)
        self.assertIn('| `0.19.1` | ✅ | ❌ runner/app.py |', text)

    def test_text_from_logs_cannot_break_out_of_the_table(self):
        hostile = dict(RESULTS['rows'][1], contract={'status': 'fail', 'step': 'x | @someone `rm` <script>' + 'y' * 200})
        row = canary.table({'pin': '0.16.0', 'rows': [hostile]}).split('\n')[2]
        self.assertEqual(row.count('|'), 4)
        self.assertNotRegex(row.split('|')[2], r'[@`<>]')
        self.assertLess(len(row), 200)

    def test_the_issue_body_carries_its_state_back_to_the_next_run(self):
        body = canary.render(RESULTS, 'https://example.test/run/1', '2026-11-02')
        self.assertEqual(canary.read_state(body), canary.state_of(RESULTS))
        self.assertIn('https://example.test/run/1', body)
        self.assertIsNone(canary.read_state('a body somebody rewrote'))
        self.assertIsNone(canary.read_state('<!-- omnigent-canary-state {oops} -->'))

    def test_comments_only_when_a_result_changes_or_a_release_appears(self):
        new = canary.state_of(RESULTS)
        self.assertEqual(canary.changes(None, new), [])
        self.assertEqual(canary.changes(new, new), [])
        worse = json.loads(json.dumps(new))
        worse['rows']['0.19.1']['contract'] = 'fail'
        self.assertEqual(canary.changes(new, worse), ['`0.19.1` server contract: pass → fail.'])
        older = json.loads(json.dumps(new))
        del older['rows']['0.19.1']
        self.assertEqual(canary.changes(older, new), ['New release `0.19.1`: server contract pass, anchors fail.'])
        gone = json.loads(json.dumps(new))
        gone['rows']['0.17.0'] = {'contract': 'pass', 'anchors': 'pass'}
        self.assertEqual(canary.changes(gone, new), [])
        moved = dict(new, pin='0.17.0')
        self.assertEqual(len(canary.changes(new, moved)), 1)

    def test_results_from_another_job_are_checked_before_use(self):
        self.assertTrue(canary.valid(RESULTS))
        for broken in (None, {}, {'pin': '0.16.0', 'rows': []}, {'pin': 'latest', 'rows': RESULTS['rows']},
                       {'pin': '0.16.0', 'rows': [{'version': '0.17.0', 'contract': {'status': 'maybe'}, 'anchors': PASS}]},
                       {'pin': '0.16.0', 'rows': [{'version': '0.17.0', 'contract': 'pass', 'anchors': PASS}]}):
            self.assertFalse(canary.valid(broken), broken)


class AnchorCheckTests(unittest.TestCase):
    FILES = ['host/connect.py', 'harnesses/hermes_native/bridge.py', 'harnesses/hermes_native/forwarder.py', 'runner/app.py',
             'runner/native/orchestration.py', 'onboarding/harness_readiness.py']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sdk = Path(self.temp.name) / 'omnigent'
        source = Path(omnigent.__file__).resolve().parent
        for relative in self.FILES:
            target = self.sdk / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)

    def test_the_pinned_sdk_fits(self):
        self.assertIsNone(anchors.check(self.sdk))

    def test_a_moved_anchor_names_the_file_being_patched(self):
        path = self.sdk / 'runner' / 'app.py'
        path.write_text(path.read_text().replace('body_type == "model_change"', 'body_type == "model_changed"'))
        code, file, line = anchors.check(self.sdk)
        self.assertEqual((code, file), ('OMNIGENT_COMPAT_ANCHOR_MISSING', 'runner/app.py'))
        self.assertIsInstance(line, int)

    def test_a_file_that_moved_away_is_reported_as_missing(self):
        (self.sdk / 'runner' / 'native' / 'orchestration.py').unlink()
        self.assertEqual(anchors.check(self.sdk)[:2], ('FILE_MISSING', 'runner/native/orchestration.py'))


if __name__ == '__main__':
    unittest.main()
