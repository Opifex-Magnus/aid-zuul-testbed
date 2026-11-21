"""Read-only CI observer. Never emits log bodies or GitHub response text.

Kubernetes log windows are gauges, not accumulated counters: overlapping polls
cannot double-count. HTTP and pagination are bounded; partial scans fail closed.
"""
import datetime as dt
import json
from pathlib import Path
import re
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

COMPONENTS = {
    'zuul': ('scheduler', 'launcher', 'web'),
    'zuul-executor': ('executor',),
    'build-farm': ('frontend', 'scheduler', 'storage', 'worker-main', 'worker-dev', 'usage', 'farm-prometheus', 'portal'),
}
REPOS = ('kristofk/AIDepartment', 'kristofk/aid-zuul-config')
ERROR = re.compile(r'\bERROR\b|"level"\s*:\s*"(?i:error)"|^E\d{4}\b')
LIMIT = 4 * 1024 * 1024
SA = Path('/var/run/secrets/kubernetes.io/serviceaccount')


def read(url, token=None, context=None, data=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'aid-ci-observer'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = 'Bearer ' + token
    with urlopen(Request(url, headers=headers, data=data), timeout=10, context=context) as response:
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError('response exceeds limit')
    return body.decode()


def stamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)).timestamp()


class GitHub:
    """Dedicated read-only App; credentials stay in memory and are never logged."""
    def __init__(self, secret=Path('/github')):
        self.secret = secret
        self.token = None
        self.expires = 0

    def __call__(self, path):
        if not self.token or time.time() >= self.expires - 300:
            # Import/sign inside the scan: even a missing signing dependency must
            # fail the checks scan rather than prevent metrics from starting.
            import jwt
            now = int(time.time())
            app_id = self.secret.joinpath('app-id').read_text().strip()
            installation = int(self.secret.joinpath('installation-id').read_text().strip())
            signed = jwt.encode({'iat': now - 60, 'exp': now + 540, 'iss': app_id},
                                self.secret.joinpath('private-key.pem').read_text(), algorithm='RS256')
            response = json.loads(read(f'https://api.github.com/app/installations/{installation}/access_tokens',
                                       signed, data=b'{}'))
            expires = stamp(response['expires_at'])
            token = response['token']
            if not isinstance(token, str) or not token or expires <= time.time() + 300:
                raise ValueError('invalid installation token lifetime')
            self.token, self.expires = token, expires
        return json.loads(read('https://api.github.com' + path, self.token))


def pages(fetch, path, key=None):
    for page in range(1, 21):
        data = fetch(path + ('&' if '?' in path else '?') + urlencode({'per_page': 100, 'page': page}))
        rows = data[key] if key else data
        yield from rows
        if len(rows) < 100:
            return
    raise ValueError('pagination limit reached')


def stuck_checks(github, zuul):
    """Only current heads of open PRs, latest check per name/app.

    Comparing the final build's end to the check's start prevents a previous
    buildset on the same SHA from making a fresh rerun look stuck.
    """
    count = 0
    for repo in REPOS:
        for pr in pages(github, f'/repos/{repo}/pulls?state=open'):
            sha = pr['head']['sha']
            for check in pages(github, f'/repos/{repo}/commits/{sha}/check-runs?filter=latest', 'check_runs'):
                if check['name'] not in ('aid/check', 'aid/gate') or check['status'] not in ('queued', 'in_progress'):
                    continue
                pipeline = check['name'].split('/')[1]
                query = urlencode({'project': repo, 'patchset': sha, 'pipeline': pipeline, 'complete': 'true', 'limit': 1})
                sets = zuul('/api/tenant/aid/buildsets?' + query)
                if not sets:
                    continue
                final = sets[0]
                end = final.get('last_build_end_time')
                start = check.get('started_at')
                if (final.get('result') and final.get('message') is not None and end and start
                        and stamp(end) >= stamp(start)):
                    count += 1
    return count


def log_counts(kube):
    samples = []
    for namespace, components in COMPONENTS.items():
        for component in components:
            selector = urlencode({'labelSelector': 'app.kubernetes.io/name=' + component})
            pods = kube(f'/api/v1/namespaces/{namespace}/pods?{selector}', True)['items']
            running = [p for p in pods if p['status']['phase'] == 'Running']
            if not running:
                raise ValueError('component missing')
            count = 0
            for pod in running:
                for container in pod['spec']['containers']:
                    query = urlencode({'container': container['name'], 'sinceSeconds': 600, 'timestamps': 'false', 'limitBytes': LIMIT})
                    body = kube(f"/api/v1/namespaces/{namespace}/pods/{quote(pod['metadata']['name'])}/log?{query}", False)
                    # Kubernetes can silently truncate at limitBytes. Do not call that healthy.
                    if len(body.encode()) >= LIMIT:
                        raise ValueError('log window truncated')
                    count += sum(bool(ERROR.search(line)) for line in body.splitlines())
            samples.append((namespace, component, count))
    return samples


class Observer:
    def __init__(self):
        self.body = b''
        self.updated = 0
        self.lock = threading.Lock()

    def poll(self, kube, github, zuul):
        lines = []
        for name, collect in [('logs', lambda: log_counts(kube)), ('checks', lambda: stuck_checks(github, zuul))]:
            try:
                result = collect()
                lines.append(f'ci_observer_scan_ok{{scan="{name}"}} 1')
                if name == 'checks':
                    lines.append(f'ci_stuck_check_runs {result}')
                else:
                    lines.extend(f'ci_component_errors_10m{{namespace="{ns}",component="{c}"}} {n}' for ns, c, n in result)
            except Exception:
                lines.append(f'ci_observer_scan_ok{{scan="{name}"}} 0')
        with self.lock:
            self.body = ('\n'.join(lines) + '\n').encode()
            self.updated = time.monotonic()

    def snapshot(self):
        with self.lock:
            return (200, self.body) if self.updated and time.monotonic() - self.updated < 300 else (503, b'')


def main():
    observer = Observer()
    context = ssl.create_default_context(cafile=str(SA / 'ca.crt'))
    def kube(path, structured):
        data = read('https://kubernetes.default.svc' + path, (SA / 'token').read_text().strip(), context)
        return json.loads(data) if structured else data
    github = GitHub()
    def zuul(path):
        return json.loads(read('http://web.zuul.svc.cluster.local:9000' + path))
    def loop():
        while True:
            observer.poll(kube, github, zuul)
            time.sleep(120)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404)
                return
            status, body = observer.snapshot()
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    threading.Thread(target=loop, daemon=True).start()
    HTTPServer(('0.0.0.0', 9103), Handler).serve_forever()


if __name__ == '__main__':
    main()
