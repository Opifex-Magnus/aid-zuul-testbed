import importlib.util
import json
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('observer', Path(__file__).with_name('observer.py'))
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class GitHubTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.secret = Path(self.directory.name)
        # Ephemeral test-only key; never a production App credential.
        subprocess.run(['openssl', 'genrsa', '-out', str(self.secret / 'private-key.pem'), '2048'],
                       check=True, capture_output=True)
        (self.secret / 'app-id').write_text('123')
        (self.secret / 'installation-id').write_text('456')
        self.github = o.GitHub(self.secret)
        self.now = 1800000000

    def test_mint_cache_and_refresh(self):
        calls, signed = [], []
        def encode(claims, key, algorithm):
            signed.append(claims)
            self.assertEqual(algorithm, 'RS256')
            self.assertEqual(key, (self.secret / 'private-key.pem').read_text())
            return 'test-jwt'
        def read(url, token, **kwargs):
            calls.append((url, token, kwargs))
            if url.endswith('/access_tokens'):
                self.assertEqual(token, 'test-jwt')
                self.assertEqual(kwargs, {'data': b'{}'})
                expiry = dt_from_stamp(self.now + 3600)
                return json.dumps({'token': 'installation-token', 'expires_at': expiry})
            self.assertEqual(token, 'installation-token')
            return '[]'
        with patch.dict(sys.modules, jwt=types.SimpleNamespace(encode=encode)), \
                patch.object(o.time, 'time', side_effect=lambda: self.now), patch.object(o, 'read', side_effect=read):
            self.assertEqual(self.github('/repos/owner/repo/pulls?state=open'), [])
            self.assertEqual(signed, [{'iat': self.now - 60, 'exp': self.now + 540, 'iss': '123'}])
            self.assertIn('/app/installations/456/access_tokens', calls[0][0])
            self.now += 3299
            self.github('/repos/owner/repo/commits/head/check-runs')
            self.assertEqual(len(signed), 1)
            self.now += 1
            self.github('/repos/owner/repo/pulls?state=open')
            self.assertEqual(len(signed), 2)

    def test_mint_and_refresh_fail_closed_without_logging(self):
        for cached in (False, True):
            if cached:
                self.github.token, self.github.expires = 'old-token', self.now + 300
            with self.subTest(cached=cached), patch.dict(sys.modules, jwt=types.SimpleNamespace(encode=lambda *a, **k: 'secret-jwt')), \
                    patch.object(o.time, 'time', return_value=self.now), \
                    patch.object(o, 'read', side_effect=RuntimeError('secret-response-token')), \
                    patch.object(o, 'log_counts', return_value=[]), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                observer = o.Observer()
                observer.poll(None, self.github, None)
                body = observer.snapshot()[1]
                self.assertIn(b'ci_observer_scan_ok{scan="checks"} 0', body)
                self.assertNotIn(b'ci_stuck_check_runs', body)
                self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')
                self.assertNotIn(b'secret', body)

    def test_http_post_and_bounds(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.object(o, 'urlopen', return_value=response) as opened:
            self.assertEqual(o.read('https://api.github.com/app/installations/456/access_tokens', 'jwt', data=b'{}'), '{}')
            request = opened.call_args.args[0]
            self.assertEqual(request.get_method(), 'POST')
            self.assertEqual(request.get_header('Content-type'), 'application/json')
            self.assertEqual(request.get_header('Authorization'), 'Bearer jwt')
            self.assertEqual(opened.call_args.kwargs['timeout'], 10)
            response.__enter__.return_value.read.assert_called_with(o.LIMIT + 1)
            response.__enter__.return_value.read.return_value = b'x' * (o.LIMIT + 1)
            with self.assertRaises(ValueError):
                o.read('https://api.github.com', 'jwt')

    @unittest.skipUnless(os.environ.get('OBSERVER_RUNTIME_AUTH_TEST'), 'real PyJWT checked in pinned scheduler image')
    def test_pinned_runtime_rs256(self):
        import jwt
        def exchange(url, token, **kwargs):
            if url.endswith('/access_tokens'):
                public = subprocess.run(['openssl', 'rsa', '-in', str(self.secret / 'private-key.pem'), '-pubout'],
                                        check=True, capture_output=True).stdout
                claims = jwt.decode(token, public, algorithms=['RS256'], issuer='123')
                self.assertLessEqual(claims['exp'] - claims['iat'], 600)
                return json.dumps({'token': 'installation-token', 'expires_at': dt_from_stamp(o.time.time() + 3600)})
            self.assertEqual(token, 'installation-token')
            return '[]'
        with patch.object(o, 'read', side_effect=exchange):
            self.assertEqual(self.github('/repos/owner/repo/pulls?state=open'), [])


def dt_from_stamp(value):
    return o.dt.datetime.fromtimestamp(value, o.dt.timezone.utc).isoformat()


class ObserverTest(unittest.TestCase):
    def test_error_formats_and_every_component(self):
        def kube(path, structured):
            if structured:
                return {'items': [{'metadata': {'name': 'pod'}, 'status': {'phase': 'Running'}, 'spec': {'containers': [{'name': 'main'}]}}]}
            return 'INFO no error occurred\nERROR problem\n{"level":"error"}\nE1119 12:01 failure\ntraceback continuation\n'
        result = o.log_counts(kube)
        self.assertEqual(len(result), sum(map(len, o.COMPONENTS.values())))
        self.assertTrue(all(n == 3 for _, _, n in result))

    def test_rule_samples_cover_each_configured_component(self):
        fixtures = json.loads(Path(__file__).with_name('rules-test.yaml').read_text())
        names = {sample['name'] for sample in fixtures['tests']}
        for namespace, components in o.COMPONENTS.items():
            for component in components:
                self.assertIn(namespace + '/' + component, names)

    def test_missing_pod_and_truncation_fail_scan(self):
        with self.assertRaises(ValueError):
            o.log_counts(lambda *args: {'items': []})
        def kube(path, structured):
            if structured:
                return {'items': [{'metadata': {'name': 'pod'}, 'status': {'phase': 'Running'}, 'spec': {'containers': [{'name': 'main'}]}}]}
            return 'x' * o.LIMIT
        with self.assertRaises(ValueError):
            o.log_counts(kube)

    def test_current_head_final_build_only(self):
        checks = [{'name': 'aid/gate', 'status': 'in_progress', 'started_at': '2026-11-19T12:00:00Z',
                   'completed_at': None, 'conclusion': None}]
        def github(path):
            if '/pulls?' in path:
                return [{'head': {'sha': 'current'}}]
            self.assertIn('/commits/current/check-runs?filter=latest', path)
            return {'check_runs': checks}
        final = {'result': 'FAILURE', 'message': 'Failed', 'last_build_end_time': '2026-11-19T12:10:00'}
        def zuul(path):
            self.assertIn('patchset=current', path)
            self.assertIn('pipeline=gate', path)
            return [final]
        with patch.object(o, 'REPOS', ('owner/repo',)):
            observer = o.Observer()
            def assert_scan(expected):
                self.assertEqual(o.stuck_checks(github, zuul), expected)
                with patch.object(o, 'log_counts', return_value=[]):
                    observer.poll(None, github, zuul)
                status, body = observer.snapshot()
                self.assertEqual(status, 200)
                self.assertIn(b'ci_observer_scan_ok{scan="checks"} 1', body)
                self.assertIn(f'ci_stuck_check_runs {expected}\n'.encode(), body)
            assert_scan(1)
            # Missing/null starts (including queued runs) cannot correlate a build.
            for state in ('queued', 'in_progress'):
                checks[0]['status'] = state
                for start in (None, ''):
                    checks[0]['started_at'] = start
                    assert_scan(0)
                del checks[0]['started_at']
                assert_scan(0)
            checks[0]['status'] = 'in_progress'
            # A fresh rerun of the same commit must not match its old finished build.
            checks[0]['started_at'] = '2026-11-19T12:11:00Z'
            self.assertEqual(o.stuck_checks(github, zuul), 0)
            checks[0]['started_at'] = '2026-11-19T12:10:00Z'
            assert_scan(1)
            checks[0]['started_at'] = '2026-11-19T12:00:00Z'
            final['message'] = None
            self.assertEqual(o.stuck_checks(github, zuul), 0)
            final['message'] = 'Failed'
            checks[0]['status'] = 'completed'
            self.assertEqual(o.stuck_checks(github, zuul), 0)

    def test_pagination_and_scan_failure(self):
        calls = []
        def fetch(path):
            calls.append(path)
            return [0] * (100 if path.endswith('&page=1') else 2)
        self.assertEqual(len(list(o.pages(fetch, '/pulls?state=open'))), 102)
        self.assertEqual(len(calls), 2)
        with self.assertRaises(ValueError):
            list(o.pages(lambda _: [0] * 100, '/pulls'))
        observer = o.Observer()
        self.assertEqual(observer.snapshot()[0], 503)
        with patch.object(o, 'log_counts', side_effect=TimeoutError), patch.object(o, 'stuck_checks', return_value=1):
            observer.poll(None, None, None)
        status, body = observer.snapshot()
        self.assertEqual(status, 200)
        self.assertIn(b'ci_observer_scan_ok{scan="logs"} 0', body)
        self.assertIn(b'ci_stuck_check_runs 1', body)
        observer.updated -= 301
        self.assertEqual(observer.snapshot()[0], 503)


if __name__ == '__main__':
    unittest.main()
