"""Exercise the adapter through the pinned upstream updateCheck and HTTP path."""
import datetime
import importlib.util
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

# The test needs no installed Zuul/requests; only requests' HTTPError interface.
class HTTPError(Exception):
    def __init__(self, status):
        self.response = types.SimpleNamespace(status_code=status)
        super().__init__(f'HTTP {status}')

sys.modules['requests'] = types.SimpleNamespace(exceptions=types.SimpleNamespace(HTTPError=HTTPError))
sys.modules['requests.exceptions'] = sys.modules['requests'].exceptions
root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('checks', root / 'deploy/zuul/server/scheduler/checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
source = Path(__file__).with_name('github_checks_14_3.py').read_text()

class Recovery(unittest.TestCase):
    def setup_connection(self, failures):
        scope = dict(requests=sys.modules['requests'], datetime=datetime,
                     utc=datetime.timezone.utc, PREVIEW_CHECKS_ACCEPT='checks',
                     get_annotated_logger=lambda log, event: log)
        exec(source, scope)
        connection = scope['GithubConnection']
        checks.install(connection)
        obj = connection()
        obj.log = logging.getLogger('test')
        obj._github_client_manager = types.SimpleNamespace(usesAppAuthentication=True)
        session = Mock()
        session.build_url.side_effect = lambda *parts: '/'.join(parts)
        def response(status=None, run_id=135584840046):
            result = Mock()
            if status:
                result.raise_for_status.side_effect = HTTPError(status)
            result.json.return_value = {'id': run_id}
            return result
        session.patch.side_effect = [response(status) for status in failures] + [response()]
        session.post.return_value = response()
        obj.getGithubClient = Mock(return_value=types.SimpleNamespace(session=session))
        obj._append_accept_header = Mock()
        return obj, session

    def report(self, obj, run_id=135584840045, completed=True, status='success'):
        return obj.updateCheck('owner/repo', 32, 'abc123', status, completed,
                               'aid/check', 'https://buildset', 'Build succeeded.',
                               None, '{"change":32}', check_run_id=run_id)

    def test_transient_404_reuses_run(self):
        obj, session = self.setup_connection([404])
        with patch.object(checks.time, 'sleep') as sleep:
            run_id, errors = self.report(obj)
        self.assertFalse(errors)
        self.assertEqual(run_id, 135584840046)
        self.assertEqual(session.patch.call_count, 2)
        self.assertEqual(session.patch.call_args.args[0], 'repos/owner/repo/check-runs/135584840045')
        session.post.assert_not_called()
        sleep.assert_called_once_with(1)
        self.assertIsNone(checks.creation.get())

    def test_persistent_404_creates_completed_replacement(self):
        obj, session = self.setup_connection([404]*3)
        with patch.object(checks.time, 'sleep') as sleep:
            run_id, errors = self.report(obj)
        self.assertFalse(errors)
        self.assertEqual(run_id, 135584840046)
        self.assertEqual(session.patch.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])
        session.post.assert_called_once()
        payload = session.post.call_args.kwargs['json']
        self.assertEqual(payload['name'], 'aid/check')
        self.assertEqual(payload['head_sha'], 'abc123')
        self.assertEqual(payload['conclusion'], 'success')
        self.assertEqual(payload['external_id'], '{"change":32}')
        self.assertEqual(payload['details_url'], 'https://buildset')
        self.assertEqual(payload['actions'], [])
        self.assertIn('completed_at', payload)
        # Subsequent updates use the returned replacement ID.
        self.report(obj, run_id=run_id)
        self.assertTrue(session.patch.call_args.args[0].endswith('/135584840046'))

    def test_other_failures_are_not_retried_or_replaced(self):
        for status in (401, 403, 422, 429, 500):
            with self.subTest(status=status):
                obj, session = self.setup_connection([status])
                with patch.object(checks.time, 'sleep') as sleep:
                    _, errors = self.report(obj)
                self.assertEqual(len(errors), 1)
                self.assertEqual(session.patch.call_count, 1)
                session.post.assert_not_called()
                sleep.assert_not_called()
                self.assertIsNone(checks.creation.get())

    def test_failed_replacement_is_reported_once(self):
        obj, session = self.setup_connection([404]*3)
        session.post.return_value.raise_for_status.side_effect = HTTPError(403)
        with patch.object(checks.time, 'sleep'):
            _, errors = self.report(obj)
        self.assertEqual(len(errors), 1)
        session.post.assert_called_once()

    def test_start_and_missing_id_use_upstream_creation(self):
        for completed, status in ((False, 'in_progress'), (True, 'failure')):
            obj, session = self.setup_connection([])
            _, errors = self.report(obj, run_id=None, completed=completed, status=status)
            self.assertFalse(errors)
            session.patch.assert_not_called()
            session.post.assert_called_once()
            payload = session.post.call_args.kwargs['json']
            self.assertEqual(payload['name'], 'aid/check')
            self.assertEqual(payload['head_sha'], 'abc123')

if __name__ == '__main__':
    unittest.main()
