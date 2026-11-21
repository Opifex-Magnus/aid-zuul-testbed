"""The test bed's checks (../README.md, "The test bed"), against a running Zuul that run.sh started.

Each check is an ordinary pull request in the test bed's copy of AIDepartment, made the way an agent makes one: a
branch, a commit and a pull request. The test bed then sends Zuul the event GitHub would send (Zuul has no route from
GitHub here), and reads the result from Zuul's API and from the commit status Zuul sets on the pull request.

  1. The layout loads: the copy of kristofk/aid-zuul-config as the config project, and AID's project, with no
     configuration error.
  2. A pull request that adds a job runs that job on itself, before it exists on main.
  3. An unknown node label fails configuration without dispatching builds (Nodepool is disabled).
  4. A pull request that defines a base job in AID's project is refused, with a configuration error.
  5. A pull request that defines a pipeline in AID's project is refused, with a configuration error.
  6. A proposed job can read the fixture farm token in every phase of check, but none of its
     pre/run/post/cleanup hooks execute in check-external: the trusted base rejects it first.

Then the gate's rows (../gate.md, "Zuul's check and gate"), each seen on the test bed's copies:

  7. A job runs no longer than its timeout.
  8. Ready means queued: a draft whose check passed stays out of gate; once ready, it enters, gate runs the full jobs
     (and not check's cheap ones), and Zuul merges it by rebase, so main stays linear.
  9. The hold label keeps a ready pull request out of gate, and takes one out that is in it; without it, it comes back.
  10. The speculative queue: two pull requests are tested at once, the second on top of the first, in a window of two.
     The first fails (a flaky test), which starts the second again without it, and the second merges. A "regate"
     comment enqueues the first again, and it merges.
  11. A ready pull request without hold enters gate when check passes on its head, with no other event. A push takes
     it out, and it comes back once check passes on the new head.

Then the secrets for jobs (deploy/zuul/job-secrets.md), when sync.sh added the test bed's jobs with a secret:

  12. A job with a secret, defined in the config project, gets it in post on main; one whose secret may not go to
     post is failed by the trusted guard before its run playbook. A job with an OIDC secret gets a token Zuul signed,
     with the claims ThunderBox's pusher checks (../publish.md, "The pusher").
  13. A pull request cannot have that secret: Zuul refuses it in check, a job inheriting the job, and a job naming the
     secret.

Checks 1 to 13 run with a copy of gate that leaves out its requirement of an approving review (sync.sh): the run's
token opens their pull requests, and nobody may approve their own. Check 14 runs alone (--loop-pull), with a copy that
keeps it (TESTBED_LOOP=1), on a pull request that the builder opened as someone else:

  14. The two-agent loop (../gate.md, "The two-agent loop"): the builder's draft passes check; ready, a comment review
     and "regate" each leave it out of gate; the reviewer's approval of a held head, and the builder's push after it,
     leave it out, as GitHub dismisses that approval; the reviewer's approval of the new head lets it in, Zuul merges
     it, and its issue closes.

Standard library only. Reads GITHUB_TOKEN and ZUUL_WEBHOOK_TOKEN from the environment.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

GITHUB_API = "https://api.github.com"


class Failure(Exception):
    pass


def log(message):
    print(message, flush=True)


def request(method, url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            text = response.read().decode()
            return response.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(text)
        except ValueError:
            return error.code, text


class GitHub:
    def __init__(self, repository, token):
        self.repository = repository
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def call(self, method, path, body=None, ok=(200, 201, 204)):
        status, data = request(method, f"{GITHUB_API}{path}", body, self.headers)
        if status not in ok:
            raise Failure(f"GitHub {method} {path}: {status} {data}")
        return data

    def repo(self, path="", **kwargs):
        method = kwargs.pop("method", "GET")
        return self.call(method, f"/repos/{self.repository}{path}", **kwargs)


class Zuul:
    def __init__(self, url, tenant, webhook_token):
        self.url = url.rstrip("/")
        self.tenant = tenant
        self.webhook_token = webhook_token

    def get(self, path, ok=(200,)):
        status, data = request("GET", f"{self.url}/api/tenant/{self.tenant}{path}")
        if status not in ok:
            raise Failure(f"Zuul GET {path}: {status} {data}")
        return status, data

    def send(self, event_type, body):
        """Posts an event as GitHub's webhook would, signed with the connection's webhook token."""
        payload = json.dumps(body).encode()
        signature = "sha1=" + hmac.new(self.webhook_token.encode(), payload, hashlib.sha1).hexdigest()
        req = urllib.request.Request(
            f"{self.url}/api/connection/github/payload", data=payload, method="POST",
            headers={"Content-Type": "application/json", "X-GitHub-Event": event_type,
                     "X-GitHub-Delivery": str(uuid.uuid4()), "X-Hub-Signature": signature})
        with urllib.request.urlopen(req, timeout=60) as response:
            if response.status != 200:
                raise Failure(f"Zuul refused the {event_type} event: {response.status}")


def wait(what, probe, timeout, interval=5):
    """Calls probe until it returns something true, and returns that."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result:
                return result
        except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
            last = error
        time.sleep(interval)
    raise Failure(f"timed out after {timeout} s waiting for {what}" + (f" (last error: {last})" if last else ""))


class Testbed:
    def __init__(self, args):
        self.args = args
        self.github = GitHub(args.repository, os.environ["GITHUB_TOKEN"])
        self.zuul = Zuul(args.zuul, args.tenant, os.environ["ZUUL_WEBHOOK_TOKEN"])
        self.run = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
        self.opened = []
        self.branches = []
        self.worktree = tempfile.mkdtemp(prefix="testbed-")
        self.git("worktree", "add", "--detach", self.worktree, "origin/main", cwd=args.checkout)
        self.git("config", "user.name", "Zuul test bed")
        self.git("config", "user.email", "testbed@zuul.invalid")

    def git(self, *args, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.worktree, capture_output=True, text=True)
        if result.returncode != 0:
            raise Failure(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout.strip()

    def commit(self, files, message, base="main"):
        """Writes the files (None deletes one; a .sh is executable) on top of origin's base branch, commits them, and
        returns the commit."""
        self.git("fetch", "--quiet", "origin", base)
        self.git("checkout", "--quiet", "--detach", f"origin/{base}")
        for path, content in files.items():
            full = os.path.join(self.worktree, path)
            if content is None:
                self.git("rm", "--quiet", path)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as handle:
                handle.write(content)
            # A script a job runs as ./<script> must be executable, in the commit too.
            os.chmod(full, 0o755 if path.endswith(".sh") else 0o644)
            self.git("add", path)
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD")

    def open_pull_request(self, name, files, title, draft=False):
        branch = f"testbed/{self.run}/{name}"
        sha = self.commit(files, title)
        self.git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
        self.branches.append(branch)
        pull = self.github.repo("/pulls", method="POST", body={
            "title": title, "head": branch, "base": "main", "body": "Made by the Zuul test bed.", "draft": draft})
        self.opened.append(pull["number"])
        if pull["head"]["sha"] != sha:
            raise Failure(f"pull request #{pull['number']} has head {pull['head']['sha']}, not {sha}")
        log(f"  opened #{pull['number']}{' as a draft' if draft else ''} ({branch} at {sha[:12]}): {title}")
        self.send_pull_request("opened", pull)
        return pull

    def push(self, pull, files, message):
        """Pushes a commit onto the pull request's branch, sends Zuul the event GitHub would, and returns the pull
        request with its new head."""
        branch = pull["head"]["ref"]
        sha = self.commit(files, message, base=branch)
        self.git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
        wait(f"#{pull['number']}'s new head on GitHub", lambda: self.pull(pull)["head"]["sha"] == sha, 120, interval=2)
        log(f"  pushed {sha[:12]} to #{pull['number']}: {message}")
        return self.send_pull_request("synchronize", pull)

    def pull(self, pull):
        """The pull request as GitHub has it now."""
        return self.github.repo(f"/pulls/{pull['number']}")

    def send_pull_request(self, action, pull, **extra):
        """Sends Zuul the pull_request event GitHub would, with the pull request as it is now."""
        body = self.pull(pull)
        self.zuul.send("pull_request", {"action": action, "number": body["number"], "pull_request": body,
                                        "repository": self.github.repo(), "sender": {"login": body["user"]["login"]},
                                        **extra})
        return body

    def mark_ready(self, pull):
        self.github.call("POST", "/graphql", body={
            "query": "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
                     "{ pullRequest { isDraft } } }",
            "variables": {"id": pull["node_id"]}})
        body = self.send_pull_request("ready_for_review", pull)
        if body["draft"]:
            raise Failure(f"#{pull['number']} is still a draft")
        log(f"  #{pull['number']}: ready for review")

    def add_label(self, pull, label):
        self.github.repo(f"/issues/{pull['number']}/labels", method="POST", body={"labels": [label]})
        self.send_pull_request("labeled", pull, label={"name": label})
        log(f"  #{pull['number']}: labelled {label}")

    def remove_label(self, pull, label):
        self.github.repo(f"/issues/{pull['number']}/labels/{urllib.parse.quote(label)}", method="DELETE")
        self.send_pull_request("unlabeled", pull, label={"name": label})
        log(f"  #{pull['number']}: {label} removed")

    def review(self, pull, event, body):
        """Submits a review of the pull request's head, as the reviewer does, and sends Zuul the event GitHub would.
        Returns the review."""
        pull = self.pull(pull)
        review = self.github.repo(f"/pulls/{pull['number']}/reviews", method="POST",
                                  body={"commit_id": pull["head"]["sha"], "event": event, "body": body})
        # GitHub's REST API says APPROVED, its webhook approved, which is what gate's trigger matches.
        sent = {**review, "state": review["state"].lower()}
        self.zuul.send("pull_request_review", {"action": "submitted", "review": sent, "pull_request": pull,
                                               "repository": self.github.repo(), "sender": review["user"]})
        log(f"  #{pull['number']}: {review['user']['login']} reviewed {pull['head']['sha'][:12]}: {review['state']}")
        return review

    def comment(self, pull, text):
        comment = self.github.repo(f"/issues/{pull['number']}/comments", method="POST", body={"body": text})
        self.zuul.send("issue_comment", {"action": "created", "issue": self.github.repo(f"/issues/{pull['number']}"),
                                         "comment": comment, "repository": self.github.repo(),
                                         "sender": {"login": comment["user"]["login"]}})
        log(f"  #{pull['number']}: commented {text!r}")

    def send_check_passed(self, pull):
        """Waits for check's success on the head, then sends Zuul the status event GitHub would for it: gate's trigger
        on check's result (on ThunderBox, its check run; on the test bed, its status, sync.sh)."""
        expect(self.commit_status(pull) == "success", f"check passed on #{pull['number']}")
        sha = pull["head"]["sha"]
        status = next(status for status in self.github.repo(f"/commits/{sha}/statuses")
                      if status["context"] == f"{self.args.tenant}/check")
        self.zuul.send("status", {"sha": sha, "name": self.args.repository, "context": status["context"],
                                  "state": status["state"], "description": status["description"],
                                  "repository": self.github.repo(), "sender": status["creator"]})

    def buildsets(self, pull, pipeline):
        """The pipeline's complete buildsets of the pull request's head, newest first."""
        query = urllib.parse.urlencode({"project": self.args.repository, "change": pull["number"],
                                        "patchset": pull["head"]["sha"], "pipeline": pipeline, "complete": "true"})
        return self.zuul.get(f"/buildsets?{query}")[1]

    def builds(self, pull, pipeline):
        """The pipeline's builds of the pull request's head, finished or not, newest first."""
        query = urllib.parse.urlencode({"project": self.args.repository, "change": pull["number"],
                                        "patchset": pull["head"]["sha"], "pipeline": pipeline})
        return self.zuul.get(f"/builds?{query}")[1]

    def result(self, pull, pipeline="check", timeout=1200, after=0):
        """Waits for a pipeline's result on the pull request's head, its buildset after the first `after` of them.
        Returns the buildset and its builds' results."""
        number = pull["number"]

        def done():
            # In gate, a pull request's buildset is started again when one ahead of it fails: its result is the one
            # it has once it has left gate.
            if pipeline == "gate" and self.in_gate(pull):
                return None
            buildsets = self.buildsets(pull, pipeline)
            return len(buildsets) > after and buildsets[0]

        buildset = wait(f"{pipeline}'s result on #{number}", done, timeout)
        _, detail = self.zuul.get(f"/buildset/{buildset['uuid']}")
        self.last_builds = detail.get("builds", [])
        results = {build["job_name"]: build["result"] for build in self.last_builds}
        log(f"  #{number}: {pipeline} {buildset['result']}, builds {results}")
        return buildset, results

    def check_result(self, pull, timeout=1200, pipeline="check"):
        """Waits for the check pipeline's result on the pull request's head; returns the buildset and its builds."""
        return self.result(pull, pipeline, timeout)

    def gate(self):
        """gate's one queue, as Zuul's status shows it: its window, and its items, each with its pull request's
        number, the item ahead of it and its jobs."""
        _, status = self.zuul.get("/status")
        pipeline = next(pipeline for pipeline in status["pipelines"] if pipeline["name"] == "gate")
        # Zuul makes a dependent pipeline's queue when a change enters it, and removes it once it is empty.
        queues = pipeline["change_queues"]
        if not queues:
            return None, []
        if len(queues) != 1:
            raise Failure(f"gate has {len(queues)} queues, not one: {[queue['name'] for queue in queues]}")
        items = [item for head in queues[0]["heads"] for item in head]
        for item in items:
            item["number"] = int(item["refs"][0]["id"].split(",")[0])
        return queues[0]["window"], items

    def in_gate(self, pull):
        return next((item for item in self.gate()[1] if item["number"] == pull["number"]), None)

    def stays_out_of_gate(self, pull, why, seconds=30):
        """Zuul has had the events: the pull request does not enter gate for a while, and gate has no buildset of it."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.in_gate(pull):
                raise Failure(f"#{pull['number']} entered gate, though {why}")
            time.sleep(3)
        expect(not self.buildsets(pull, "gate"), f"#{pull['number']} stays out of gate: {why}")

    def running_in_gate(self, pull, timeout=600):
        """Waits until the pull request is in gate with a build running, and returns its item."""
        def running():
            item = self.in_gate(pull)
            return item and running_build(item) and item
        item = wait(f"#{pull['number']}'s build in gate", running, timeout, interval=2)
        log(f"  ok: #{pull['number']} is in gate, its build running")
        return item

    def merged(self, pull, timeout=300):
        """Waits until GitHub shows the pull request merged; returns it."""
        return wait(f"#{pull['number']} merged", lambda: (body := self.pull(pull))["merged"] and body, timeout)

    def main_head(self):
        return self.github.repo("/commits/main")["sha"]

    def commit_status(self, pull, timeout=120, pipeline="check"):
        """The state of the commit status a Zuul pipeline set on the pull request's head (tenant/pipeline)."""
        context = f"{self.args.tenant}/{pipeline}"

        def final():
            statuses = self.github.repo(f"/commits/{pull['head']['sha']}/statuses")
            mine = [status for status in statuses if status["context"] == context]
            return mine and mine[0]["state"] != "pending" and mine[0]

        status = wait(f"Zuul's {context} status on #{pull['number']}", final, timeout)
        log(f"  #{pull['number']}: GitHub shows {context} {status['state']}: {status['description']}")
        return status["state"]

    def external_comment(self, pull):
        comment = self.github.repo(f"/issues/{pull['number']}/comments", method="POST", body={"body": "check-external"})
        issue = self.github.repo(f"/issues/{pull['number']}")
        self.zuul.send("issue_comment", {"action": "created", "issue": issue, "comment": comment,
                                       "repository": self.github.repo(), "sender": comment["user"]})

    def comments(self, pull):
        return [comment["body"] for comment in self.github.repo(f"/issues/{pull['number']}/comments")]

    def job_on_main(self, name):
        status, _ = self.zuul.get(f"/job/{name}", ok=(200, 404))
        return status == 200

    def cleanup(self):
        for number in self.opened:
            try:
                if self.github.repo(f"/pulls/{number}")["state"] == "closed":
                    continue
                self.github.repo(f"/pulls/{number}", method="PATCH", body={"state": "closed"})
            except Failure as error:
                log(f"  could not close #{number}: {error}")
        for branch in self.branches:
            try:
                self.github.repo(f"/git/refs/heads/{branch}", method="DELETE")
            except Failure as error:
                log(f"  could not delete {branch}: {error}")
        subprocess.run(["git", "worktree", "remove", "--force", self.worktree], cwd=self.args.checkout)


def running_build(item):
    """Whether a build of an item in Zuul's status has started and not ended."""
    return any(job["start_time"] and not job["end_time"] for job in item["jobs"])


def expect(condition, message):
    if not condition:
        raise Failure(message)
    log(f"  ok: {message}")


def layout_loads(bed):
    log("1. The layout loads")
    names = {"check", "check-external", "gate"}

    def loaded():
        _, pipelines = bed.zuul.get("/pipelines", ok=(200, 404))
        return isinstance(pipelines, list) and names <= {pipeline["name"] for pipeline in pipelines}

    wait("the tenant's pipelines", loaded, 600)
    _, errors = bed.zuul.get("/config-errors")
    # The API includes nonfatal deprecations alongside errors in the pinned Zuul runtime.
    for error in errors:
        if error.get("severity") == "warning":
            log(f"  configuration warning: {error.get('error', '')}")
    errors = [error for error in errors if error.get("severity") != "warning"]
    expect(errors == [], f"the tenant has no configuration error {[error.get('error', '')[:300] for error in errors]}")
    _, projects = bed.zuul.get("/projects")
    found = {(project["canonical_name"], project["type"]) for project in projects}
    expect((f"github.com/{bed.args.config_repository}", "config") in found,
           f"{bed.args.config_repository} is the config project")
    expect((f"github.com/{bed.args.repository}", "untrusted") in found, f"{bed.args.repository} is AID's untrusted project")
    _, jobs = bed.zuul.get("/jobs")
    expect({"base", "aid-bazel", "aid-script", "aid-seam-lint"} <= {job["name"] for job in jobs},
           "the base job and AID's job library are loaded")
    # The nightly (#1350), once the config project has its periodic pipelines, one per time.
    _, pipelines = bed.zuul.get("/pipelines")
    for name in ("periodic-0200", "periodic-0300"):
        if name not in {pipeline["name"] for pipeline in pipelines}:
            continue
        _, project = bed.zuul.get(f"/project/github.com/{bed.args.repository}")
        periodic = {job[0]["name"] for config in project.get("configs", []) for pipeline in config.get("pipelines", [])
                    if pipeline["name"] == name for job in pipeline.get("jobs", [])}
        expect("aid-nightly" in periodic, f"AID's project runs aid-nightly in {name} ({sorted(periodic)})")
    if bed.args.secret:
        expect({"testbed-secret-probe", "testbed-secret-guard-probe", "testbed-oidc-probe"} <= {job["name"] for job in jobs},
               "the test bed's jobs with a secret are loaded: Zuul decrypted the fixture with the imported key")
        # The key is PEM, not JSON.
        with urllib.request.urlopen(f"{bed.zuul.url}/api/tenant/{bed.zuul.tenant}/key/{bed.args.config_repository}.pub",
                                    timeout=60) as response:
            status, key = response.status, response.read().decode()
        fixture = subprocess.run(["openssl", "pkey", "-in", bed.args.project_key, "-pubout"],
                                 capture_output=True, text=True, check=True).stdout
        expect(status == 200 and der(key) == der(fixture), "the config copy's key in Zuul is the test bed's own")


NEW_JOB = """\
# Added by the test bed's pull request: a job of AID's, from the library, and the check pipeline running it.
- job:
    name: aid-testbed-hello
    parent: aid-bazel
    description: Builds the test bed's one target.
    vars:
      bazel_command: build
      bazel_targets: ["//testbed/hello:hello"]

- project:
    check:
      jobs:
        - aid-testbed-hello
"""

NEW_TARGET = """\
genrule(
    name = "hello",
    outs = ["hello.txt"],
    cmd = "echo hello > $@",
)
"""


def new_job_runs_speculatively(bed):
    log("2. A pull request that adds a job runs it on itself")
    expect(not bed.job_on_main("aid-testbed-hello"), "main has no job aid-testbed-hello")
    pull = bed.open_pull_request("new-job", {
        "ci/zuul/zuul.d/testbed-hello.yaml": NEW_JOB,
        "testbed/hello/BUILD.bazel": NEW_TARGET,
    }, "A new job: aid-testbed-hello")
    buildset, builds = bed.check_result(pull)
    expect(builds.get("aid-testbed-hello") == "SUCCESS", "the new job ran on its own pull request, and passed")
    expect(builds == {**{job: "SUCCESS" for job in CHEAP}, "aid-testbed-hello": "SUCCESS"},
           f"check ran the cheap jobs {CHEAP} beside it, and they passed")
    expect(buildset["result"] == "SUCCESS", "check passed")
    expect(bed.commit_status(pull) == "success", "Zuul reported success on the pull request")
    expect(not bed.job_on_main("aid-testbed-hello"), "main still has no job aid-testbed-hello: it ran speculatively")


BASE_JOB = """\
- job:
    name: testbed-base
    parent: null
    description: A base job of AID's own, which would replace the config project's checkout.
"""


def untrusted_base_job_is_refused(bed):
    log("4. A pull request that defines a base job in AID's project is refused")
    pull = bed.open_pull_request("untrusted-base-job", {"ci/zuul/zuul.d/testbed-base.yaml": BASE_JOB},
                                 "A base job in AID's untrusted project")
    expect(bed.commit_status(pull, timeout=600) == "failure", "Zuul reported failure on the pull request")
    comments = wait(f"Zuul's comment on #{pull['number']}", lambda: bed.comments(pull), 120)
    # Zuul's own words for it (zuul/exceptions.py, BaseJobNotPermittedError).
    expect(any("Base jobs may only be defined in config projects" in " ".join(body.split()) for body in comments),
           f"its comment says why: {comments[-1][:300]!r}")


UNKNOWN_LABEL_JOB = """\
- job:
    name: aid-testbed-unknown-label
    parent: aid-script
    nodeset:
      nodes:
        - name: node
          label: testbed-nonexistent-label
    vars:
      script: scripts/seam-lint.sh

- project:
    check:
      jobs:
        - aid-testbed-unknown-label
"""


def unknown_label_is_refused(bed):
    log("3. An unknown label is a configuration error, without a Nodepool request")
    library = bed.git("show", "origin/main:ci/zuul/zuul.d/jobs.yaml")
    # Compatibility with older snapshots; current production uses cleanup post-runs (#1995).
    library = re.sub(r"(?m)^    cleanup-run: (.+)$",
                     r"    post-run:\n      - name: \1\n        cleanup: true", library)
    pull = bed.open_pull_request("unknown-label", {
        "ci/zuul/zuul.d/testbed-unknown.yaml": UNKNOWN_LABEL_JOB,
        "ci/zuul/zuul.d/jobs.yaml": library,
    }, "Unknown node label cannot fall back to Nodepool")
    expect(bed.commit_status(pull, timeout=600) == "failure", "unknown label fails check within its deadline")
    # The probe isolates label validation from unrelated cleanup-run warnings (#1889).
    comments = wait(f"Zuul's missing-label diagnostic on #{pull['number']}",
                    lambda: [body for body in bed.comments(pull)
                             if 'The label "testbed-nonexistent-label" was not found.' in " ".join(body.split())], 120)
    expect(bool(comments), f"Zuul reports the missing label: {comments[-1][:300]!r}")
    _, builds = bed.check_result(pull, timeout=120)
    expect(not builds, f"no job was dispatched for an invalid layout: {builds}")


PIPELINE = """\
- pipeline:
    name: testbed-untrusted
    manager: independent
    trigger:
      github:
        - event: pull_request
          action: comment
          comment: ^never$
"""


def untrusted_pipeline_is_refused(bed):
    log("5. A pull request that defines a pipeline in AID's project is refused")
    pull = bed.open_pull_request("untrusted-pipeline", {"ci/zuul/zuul.d/testbed-pipeline.yaml": PIPELINE},
                                 "A pipeline in AID's untrusted project")
    expect(bed.commit_status(pull, timeout=600) == "failure", "Zuul reported failure on the pull request")
    comments = wait(f"Zuul's comment on #{pull['number']}", lambda: bed.comments(pull), 120)
    # Zuul's own words for it (zuul/exceptions.py, PipelineNotPermittedError).
    expect(any("Pipelines may not be defined in untrusted repos" in " ".join(body.split()) for body in comments),
           f"its comment says why: {comments[-1][:300]!r}")


def container_text(container, path):
    result = subprocess.run(["docker", "exec", os.environ[container], "cat", path],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise Failure(f"cannot read test fixture {path}: {result.stderr[:200]}")
    return result.stdout


def nodes_text(path):
    """The file on each separate check/compute node (compose.yaml), one after the other; a node
    whose job did not run there has none."""
    texts = []
    for container in ("TESTBED_NODE_ID", "TESTBED_NODE_2_ID", "TESTBED_COMPUTE_ID", "TESTBED_COMPUTE_2_ID"):
        result = subprocess.run(["docker", "exec", os.environ[container], "cat", path],
                                capture_output=True, text=True, timeout=30)
        if result.returncode and "No such file" not in result.stderr:
            raise Failure(f"cannot read test fixture {path}: {result.stderr[:200]}")
        texts.append(result.stdout if result.returncode == 0 else "")
    return "".join(texts)


def external_job_cannot_read_farm(bed):
    log("6. check-external is rejected before every untrusted hook, including post and cleanup")
    job = "aid-testbed-farm-probe"
    files = {"ci/zuul/zuul.d/testbed-farm.yaml": f"""\
- job:
    name: {job}
    parent: aid-script
    pre-run: ci/zuul/playbooks/farm-pre.yaml
    run: ci/zuul/playbooks/farm-run.yaml
    post-run:
      - ci/zuul/playbooks/farm-post.yaml
      - name: ci/zuul/playbooks/farm-cleanup.yaml
        cleanup: true
- project:
    check:
      jobs: [{job}]
    check-external:
      jobs: [{job}]
"""}
    for phase in ("pre", "run", "post", "cleanup"):
        files[f"ci/zuul/playbooks/farm-{phase}.yaml"] = """\
- hosts: all
  gather_facts: false
  tasks:
    - name: Read the raw fixture credential from proposed code
      ansible.builtin.shell: |
        printf '{{ zuul.pipeline }}:PHASE:' >> /tmp/farm-hook-probe
        cat /var/run/farm/token >> /tmp/farm-hook-probe
""".replace("PHASE", phase)
    pull = bed.open_pull_request("external-farm-probe", files, "Probe the external-job farm boundary in every hook")
    _, builds = bed.check_result(pull)
    expect(builds.get(job) == "SUCCESS", "the proposed probe actually executes in check")
    before = nodes_text("/tmp/farm-hook-probe")
    expected = {f"check:{phase}:testbed-farm-credential" for phase in ("pre", "run", "post", "cleanup")}
    expect(set(before.splitlines()) == expected and len(before.splitlines()) == 4,
           "all four untrusted hooks can read the raw token fixture in check")
    bed.external_comment(pull)
    buildset, builds = bed.check_result(pull, pipeline="check-external")
    expect(builds.get(job) == "FAILURE" and buildset["result"] == "FAILURE",
           "the external probe fails without retries")
    build = next(build for build in bed.last_builds if build["job_name"] == job)
    build_uuid = uuid.UUID(build["uuid"]).hex
    output = container_text("TESTBED_EXECUTOR_ID", f"/var/lib/zuul/builds/{build_uuid}/work/logs/job-output.txt")
    expect("check-external is blocked until a credential-free node is available" in output,
           "the failure is the trusted pipeline guard, not a broken job or node")
    expect(nodes_text("/tmp/farm-hook-probe") == before,
           "no external pre/run/post/cleanup hook executed or read the token")
    expect(bed.commit_status(pull, pipeline="check-external") == "failure", "the external failure is reported")


# What check and gate run (ci/zuul/zuul.d/project.yaml): the cheap jobs, and the full ones. Gate's plan names the jobs
# that run; on the test bed its stand-in (sync.sh) names the full job alone, and Zuul skips gate's other jobs.
CHEAP = ["aid-seam-lint", "aid-seam-lint-test", "aid-apps-lint", "aid-apps-lint-test", "aid-cheap-checks"]
FULL = ["aid-bazel-everything"]
PLANNED = ["aid-gate-plan", *FULL]


def gate_passed(builds):
    """The plan and the jobs it named passed, and gate's other jobs, if listed, were skipped."""
    return (all(builds.get(job) == "SUCCESS" for job in PLANNED)
            and all(result == "SKIPPED" for job, result in builds.items() if job not in PLANNED))

TIMEOUT_JOB = """\
# Added by the test bed's pull request: a job that runs longer than its timeout.
- job:
    name: aid-testbed-timeout
    parent: aid-script
    timeout: 30
    vars:
      script: ci/testbed-sleep.sh

- project:
    check:
      jobs:
        - aid-testbed-timeout
"""

SLEEP = """\
#!/usr/bin/env bash
sleep 600
"""


def job_timeout(bed):
    log("7. A job runs no longer than its timeout")
    pull = bed.open_pull_request("timeout", {"ci/zuul/zuul.d/testbed-timeout.yaml": TIMEOUT_JOB,
                                             "ci/testbed-sleep.sh": SLEEP}, "A job that outlives its timeout")
    started = time.monotonic()
    buildset, builds = bed.check_result(pull)
    expect(builds.get("aid-testbed-timeout") == "TIMED_OUT", "the job was stopped at its timeout")
    expect(time.monotonic() - started < 600, "well before its script would have ended")
    expect(buildset["result"] == "FAILURE", "and check failed")


def behaviour(name, lines):
    """A pull request's testbed/gate/<name>.txt, which gate's stand-in test follows (workspace/gate_test.sh)."""
    return {f"testbed/gate/{name}.txt": "".join(f"{line}\n" for line in lines)}


def linear(bed, old, new, pull):
    """main went from old to new by the pull request's commits, rebased: one parent each, the first on old."""
    compare = bed.github.repo(f"/compare/{old}...{new}")
    commits = compare["commits"]
    expect(compare["status"] == "ahead" and compare["behind_by"] == 0,
           f"main moved forward from {old[:12]} to {new[:12]}")
    expect(all(len(commit["parents"]) == 1 for commit in commits), "with no merge commit: main stays linear")
    expect(commits[0]["parents"][0]["sha"] == old, "its first new commit sits on main as it was")
    messages = [commit["commit"]["message"].split("\n")[0] for commit in commits]
    expect(pull["title"] in messages, f"and carries the pull request's commit: {messages}")


def ready_means_queued(bed):
    log("8. Ready means queued, gate runs the full jobs, and Zuul merges by rebase")
    pull = bed.open_pull_request("ready", behaviour("ready", ["# ready means queued"]),
                                 "Gate: ready means queued", draft=True)
    bed.send_check_passed(pull)
    bed.stays_out_of_gate(pull, "it is a draft")
    old = bed.main_head()
    bed.mark_ready(pull)
    buildset, builds = bed.result(pull, "gate")
    expect(gate_passed(builds), f"gate ran its plan and the full jobs it named {FULL}, not check's, and they passed")
    expect(buildset["result"] == "SUCCESS", "gate passed")
    expect(bed.commit_status(pull, pipeline="gate") == "success", "Zuul reported gate's success on the pull request")
    merged = bed.merged(pull)
    expect(merged["merged_by"] is not None, f"Zuul merged it (as {merged['merged_by']['login']})")
    linear(bed, old, bed.main_head(), pull)


def hold(bed):
    log("9. The hold label keeps a pull request out of gate, and takes it out")
    until = int(time.time()) + 900
    pull = bed.open_pull_request("hold", behaviour("hold", [f"{until} sleep 60"]), "Gate: hold", draft=True)
    bed.add_label(pull, "hold")
    bed.mark_ready(pull)
    bed.send_check_passed(pull)
    bed.stays_out_of_gate(pull, "it has the hold label")
    bed.remove_label(pull, "hold")
    bed.running_in_gate(pull)
    bed.add_label(pull, "hold")
    wait(f"#{pull['number']} out of gate", lambda: not bed.in_gate(pull), 120, interval=2)
    log(f"  ok: labelling hold took #{pull['number']} out of gate")
    held = wait("gate's record of it", lambda: bed.buildsets(pull, "gate"), 60)
    expect(held[0]["result"] != "SUCCESS", f"its buildset there ended {held[0]['result']}, not SUCCESS")
    statuses = [status for status in bed.github.repo(f"/commits/{pull['head']['sha']}/statuses")
                if status["context"] == f"{bed.args.tenant}/gate"]
    expect(not bed.pull(pull)["merged"], "it did not merge")
    log(f"  #{pull['number']}: GitHub shows aid/gate {statuses[0]['state'] if statuses else 'nothing'} after the hold")
    bed.remove_label(pull, "hold")
    buildset, _ = bed.result(pull, "gate", after=len(held))
    expect(buildset["result"] == "SUCCESS", "without hold it entered gate again, and passed")
    bed.merged(pull)
    log(f"  ok: and #{pull['number']} merged")


def speculative_queue(bed):
    log("10. Two pull requests at once, a flaky failure, and regate")
    # The first's test fails until flaky_until (a flaky test), long enough for its first gate run and short enough
    # for the regate after it; both sleep, so that their builds are seen running at once.
    sleep_until = int(time.time()) + 900
    flaky_until = int(time.time()) + 300
    first = bed.open_pull_request("first", behaviour("first", [f"{sleep_until} sleep 30", f"{flaky_until} fail"]),
                                  "Gate: the first, flaky", draft=True)
    second = bed.open_pull_request("second", behaviour("second", [f"{sleep_until} sleep 30"]),
                                   "Gate: the second", draft=True)
    for pull in (first, second):
        bed.send_check_passed(pull)
    bed.mark_ready(first)
    bed.mark_ready(second)

    def both_running():
        window, items = bed.gate()
        mine = {item["number"]: item for item in items}
        running = [pull["number"] in mine and running_build(mine[pull["number"]]) for pull in (first, second)]
        return all(running) and (window, mine)

    window, mine = wait("both pull requests' builds running in gate at once", both_running, 600, interval=2)
    expect(window == 2, "gate's window is two")
    expect(mine[second["number"]]["item_ahead"] == mine[first["number"]]["id"],
           f"#{second['number']} is tested on top of #{first['number']}, whose build runs at the same time")
    buildset, _ = bed.result(first, "gate")
    expect(buildset["result"] == "FAILURE", f"#{first['number']}'s flaky test failed it in gate")
    expect(not bed.pull(first)["merged"], f"#{first['number']} did not merge")
    expect(bed.commit_status(first, pipeline="gate") == "failure", "Zuul reported gate's failure on it")
    comments = wait(f"Zuul's comment on #{first['number']}", lambda: bed.comments(first), 120)
    expect(any("aid-bazel-everything" in body for body in comments), "with a comment naming the job that failed")
    buildset, _ = bed.result(second, "gate")
    expect(buildset["result"] == "SUCCESS", f"#{second['number']} was tested again without it, and passed")
    builds = [build for build in bed.builds(second, "gate") if build["job_name"] == FULL[0]]
    expect(len(builds) >= 2, f"#{second['number']} ran {FULL[0]} again: {[build['result'] for build in builds]}")
    bed.merged(second)
    log(f"  ok: #{second['number']} merged")
    if time.time() < flaky_until:
        time.sleep(flaky_until - time.time() + 5)
    bed.comment(first, "regate")
    buildset, _ = bed.result(first, "gate", after=1)
    expect(buildset["result"] == "SUCCESS", f"regate enqueued #{first['number']} again, and it passed")
    bed.merged(first)
    log(f"  ok: #{first['number']} merged")


def new_head_comes_back(bed):
    log("11. A ready pull request enters gate when check passes on it, and again after a push")
    until = int(time.time()) + 900
    pull = bed.open_pull_request("new-head", behaviour("new-head", [f"{until} sleep 60"]), "Gate: a new head")
    bed.stays_out_of_gate(pull, "check has not passed on it yet", seconds=10)
    bed.send_check_passed(pull)
    bed.running_in_gate(pull)
    pull = bed.push(pull, behaviour("new-head-2", ["# a second commit"]), "Gate: a new head, pushed while in gate")
    wait(f"#{pull['number']} out of gate", lambda: not bed.in_gate(pull), 120, interval=2)
    log(f"  ok: the push took #{pull['number']} out of gate")
    expect(bed.commit_status(pull) == "success", f"check passed on #{pull['number']}'s new head")
    bed.stays_out_of_gate(pull, "Zuul has not yet heard that check passed on its new head", seconds=20)
    bed.send_check_passed(pull)
    buildset, builds = bed.result(pull, "gate")
    expect(buildset["result"] == "SUCCESS" and gate_passed(builds),
           "check's success alone brought the new head into gate, with no ready, label or comment event, and it passed")
    bed.merged(pull)
    log(f"  ok: #{pull['number']} merged")


def der(pem):
    """A public key in PEM, as its DER bytes: the same key, whatever the line breaks."""
    body = "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))
    return base64.b64decode(body)


def post_job_gets_secret(bed):
    log("12. A job with a secret gets it in post, on main; the guard fails one whose secret may not go there")
    main = bed.github.repo("/commits/main")
    repository = bed.github.repo()
    before = main["parents"][0]["sha"] if main["parents"] else "0" * 40
    bed.zuul.send("push", {"ref": "refs/heads/main", "before": before, "after": main["sha"], "commits": [],
                           "repository": repository, "sender": {"login": repository["owner"]["login"]}})
    query = urllib.parse.urlencode({"project": bed.args.repository, "pipeline": "post", "complete": "true"})
    names = {"testbed-secret-probe", "testbed-secret-guard-probe", "testbed-oidc-probe"}

    def done():
        _, builds = bed.zuul.get(f"/builds?{query}")
        # Zuul 14 gives each build its ref as an object: {project, ref, newrev, ...}.
        mine = [build for build in builds if (build.get("ref") or {}).get("newrev") == main["sha"]]
        return names <= {build["job_name"] for build in mine} and mine

    builds = {build["job_name"]: build for build in wait("post's builds on main", done, 900)}
    log(f"  post on main at {main['sha'][:12]}: " + str({name: build["result"] for name, build in builds.items()}))
    expect(all(build["ref"]["ref"] == "refs/heads/main" and not build["ref"].get("change") for build in builds.values()),
           "post ran on main, a ref, not a change")

    def output(name):
        build_uuid = uuid.UUID(builds[name]["uuid"]).hex
        return container_text("TESTBED_EXECUTOR_ID", f"/var/lib/zuul/builds/{build_uuid}/work/logs/job-output.txt")

    probe = output("testbed-secret-probe")
    digest = hashlib.sha256(bed.args.secret.encode()).hexdigest()
    expect(builds["testbed-secret-probe"]["result"] == "SUCCESS", "the job that may have its secret passed")
    expect(f"testbed secret sha256 {digest}" in probe, "the value that reached it is the fixture's, by its SHA-256")
    _, found = bed.zuul.get(f"/build/{builds['testbed-secret-probe']['uuid']}")
    artifacts = {artifact["name"]: artifact.get("metadata", {}) for artifact in found.get("artifacts", [])}
    expect(artifacts.get("testbed-secret", {}).get("sha256") == digest,
           "the builds API shows the same, as the build's artifact: how ThunderBox's check is read")
    expect(bed.args.secret not in probe, "the value itself is not in its log")
    guarded = output("testbed-secret-guard-probe")
    expect(builds["testbed-secret-guard-probe"]["result"] == "FAILURE", "the job whose secret may not go to post failed")
    expect("has a secret, and runs only on a merged ref" in " ".join(guarded.split()),
           "the guard failed it, not anything else")
    expect("testbed secret sha256" not in guarded, "its run playbook, which uses the secret, never ran")
    oidc_token_is_the_pushers(bed, builds["testbed-oidc-probe"])


# What ThunderBox's pusher checks of a token (../publish.md, "The pusher"): Zuul's signature by a key Zuul publishes, and
# the claims that make it a publishing job's. In the executor's image, which has Zuul's own PyJWT.
VERIFY = """
import json, sys, jwt
given = json.load(sys.stdin)
key = jwt.PyJWKSet.from_dict(given["jwks"])[jwt.get_unverified_header(given["token"])["kid"]].key
print(json.dumps(jwt.decode(given["token"], key, algorithms=["RS256"], audience="thunderbox-registry")))
"""


def oidc_token_is_the_pushers(bed, build):
    expect(build["result"] == "SUCCESS", "the job with an OIDC secret passed")
    _, found = bed.zuul.get(f"/build/{build['uuid']}")
    artifacts = {artifact["name"]: artifact.get("metadata", {}) for artifact in found.get("artifacts", [])}
    token = artifacts.get("testbed-pusher", {}).get("token", "")
    expect(token.count(".") == 2, "it returned the token Zuul signed for it")
    _, configuration = request("GET", f"{bed.zuul.url}/.well-known/openid-configuration")
    # The keys at <iss>/oidc/jwks, as the pusher fetches them: the token's iss is web by its service name, which is this
    # zuul-web too, reached from here at bed.zuul.url.
    _, jwks = request("GET", f"{bed.zuul.url}/oidc/jwks")
    result = subprocess.run(["docker", "exec", "-i", os.environ["TESTBED_EXECUTOR_ID"], "python3", "-c", VERIFY],
                            input=json.dumps({"token": token, "jwks": jwks}), capture_output=True, text=True, timeout=60)
    expect(result.returncode == 0, f"its signature is by a key at Zuul's jwks_uri, its audience the pusher's: {result.stderr[-300:]}")
    claims = json.loads(result.stdout)
    log(f"  the token's claims: { {key: claims[key] for key in ('iss', 'sub', 'pipeline', 'job-name', 'tenant', 'aud')} }")
    # The secret's own issuer (#1637), which the tenant allows, not Zuul's [web] root: the keys are the same.
    expect(claims["iss"] == "http://web:9000" != configuration["issuer"],
           "its issuer is the secret's, zuul-web by its service name, not [web] root")
    expect(claims["sub"] == f"secret:{bed.zuul.tenant}/github.com/{bed.args.config_repository}/testbed-pusher",
           "its subject names the tenant, the config project and the secret")
    expect((claims["pipeline"], claims["job-name"], claims["tenant"]) == ("post", "testbed-oidc-probe", bed.zuul.tenant),
           "it names the pipeline and the job that got it")
    expect(claims["exp"] - claims["iat"] == 600, "it lasts the secret's ttl")


SECRET_IN_CHECK = """\
- project:
    check:
      jobs:
        - testbed-secret-probe
"""

SECRET_CHILD = """\
- job:
    name: testbed-secret-child
    parent: testbed-secret-probe
    run: ci/zuul/playbooks/script.yaml
- project:
    check:
      jobs:
        - testbed-secret-child
"""

SECRET_NAMED = """\
- job:
    name: testbed-secret-taker
    parent: aid-script
    # A playbook of its own, which the secret goes to: Zuul checks a secret's project on the playbooks it reaches, in
    # any pipeline. Without one the secret reaches nothing, and only post-review refuses the job.
    run: ci/zuul/playbooks/script.yaml
    secrets:
      - name: testbed_secret
        secret: testbed-secret
    vars:
      script: scripts/seam-lint.sh
      script_args: [--project, aid]
- project:
    check:
      jobs:
        - testbed-secret-taker
"""


def pull_request_cannot_have_secret(bed):
    log("13. A pull request cannot have the secret")
    cases = [
        ("secret-in-check", SECRET_IN_CHECK, "The job with the secret in check",
         # Zuul's words (model.py, the job graph's pending errors).
         "Pre-review pipeline check does not allow post-review job testbed-secret-probe"),
        ("secret-child", SECRET_CHILD, "A job of AID's inheriting the job with the secret",
         'The parent of job "testbed-secret-child", "testbed-secret-probe" is final and can not act as a parent'),
        ("secret-named", SECRET_NAMED, "A job of AID's naming the config project's secret",
         "Unable to use secret testbed-secret. Secrets must be defined in the same project in which they are used"),
    ]
    for name, text, title, words in cases:
        pull = bed.open_pull_request(name, {f"ci/zuul/zuul.d/testbed-{name}.yaml": text}, title)
        expect(bed.commit_status(pull, timeout=600) == "failure", f"{title}: Zuul reported failure")
        comments = wait(f"Zuul's comment on #{pull['number']}", lambda: bed.comments(pull), 120)
        expect(any(words in " ".join(body.split()) for body in comments), f"{title}: refused, {words!r}")
        _, builds = bed.zuul.get(f"/builds?project={urllib.parse.quote(bed.args.repository)}&change={pull['number']}")
        expect(not [build for build in builds if build["job_name"].startswith("testbed-secret")
                    and build["result"] == "SUCCESS"], f"{title}: no job of the pull request's with the secret ran")
def two_agent_loop(bed, number):
    log("14. The two-agent loop: the builder's pull request enters gate only once the reviewer approved its head")
    pull = bed.pull({"number": number})
    expect(pull["state"] == "open" and pull["draft"], f"the builder's #{number} is an open draft")
    bed.opened.append(number)
    query = ("query($o: String!, $r: String!, $n: Int!) { repository(owner: $o, name: $r) { pullRequest(number: $n) "
             "{ closingIssuesReferences(first: 5) { nodes { number } } } } }")
    owner, name = bed.args.repository.split("/")
    closes = bed.github.call("POST", "/graphql",
                             body={"query": query, "variables": {"o": owner, "r": name, "n": number}})
    issues = [node["number"] for node in
              closes["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]
    expect(len(issues) == 1 and bed.github.repo(f"/issues/{issues[0]}")["state"] == "open",
           f"it closes one open issue, #{issues[0] if issues else None}")
    # What GitHub sent when the builder opened it: check runs on it.
    bed.send_pull_request("opened", pull)
    bed.send_check_passed(pull)
    # A clean round as a comment review, as the loop's reviewer posted it before Zuul: no approval.
    comment = bed.review(pull, "COMMENT", "Round 1: nothing blocks.")
    expect(comment["user"]["login"] != pull["user"]["login"],
           f"the reviewer, {comment['user']['login']}, is not the builder, {pull['user']['login']}")
    bed.mark_ready(pull)
    bed.stays_out_of_gate(pull, "it is ready and check passed, but nobody approved it")
    bed.comment(pull, "regate")
    bed.stays_out_of_gate(pull, "regate does not stand in for an approval")
    # The reviewer approves, the builder answers a comment with a push: the approval was of the head before it.
    bed.add_label(pull, "hold")
    approved = bed.review(pull, "APPROVE", "Round 2: nothing blocks.")
    bed.stays_out_of_gate(pull, "it is approved, but held")
    pull = bed.push(pull, behaviour("loop", ["# the builder's answer to a comment, after the approval"]),
                    "Loop: answer a comment after the approval")
    dismissed = wait("GitHub's dismissal of the approval of the old head", lambda: next(
        (review for review in bed.github.repo(f"/pulls/{number}/reviews")
         if review["id"] == approved["id"] and review["state"] == "DISMISSED"), None), 60, interval=3)
    expect(dismissed["commit_id"] != pull["head"]["sha"],
           "the push dismissed the approval of the old head (main's protection: dismiss stale reviews)")
    bed.send_check_passed(pull)
    bed.remove_label(pull, "hold")
    bed.stays_out_of_gate(pull, "its one approval was of the head before the push")
    old = bed.main_head()
    bed.review(pull, "APPROVE", "Round 3: nothing blocks.")
    buildset, builds = bed.result(pull, "gate")
    expect(buildset["result"] == "SUCCESS" and gate_passed(builds),
           "the reviewer's approval of its head let it into gate, and it passed")
    merged = bed.merged(pull)
    expect(merged["merged_by"] is not None, f"Zuul merged it (as {merged['merged_by']['login']})")
    linear(bed, old, bed.main_head(), pull)
    # GitHub closed no issue for a merge by the workflow's token on the test bed (run 43622489690): the pull request
    # still named it as the issue it closes. On ThunderBox, Zuul merges as its App. So the issue is closed here, as
    # completed, once the merge is seen.
    linked = bed.github.call("POST", "/graphql",
                             body={"query": query, "variables": {"o": owner, "r": name, "n": number}})
    expect([node["number"] for node in linked["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]
           == issues, f"the merged pull request still closes #{issues[0]}")
    issue = bed.github.repo(f"/issues/{issues[0]}")
    if issue["state"] == "open":
        log(f"  #{issues[0]}: GitHub did not close it for the merge by {merged['merged_by']['login']}: closing it")
        bed.github.repo(f"/issues/{issues[0]}/comments", method="POST",
                        body={"body": f"Done: #{number} was reviewed, gated and merged by Zuul."})
        issue = bed.github.repo(f"/issues/{issues[0]}", method="PATCH",
                                body={"state": "closed", "state_reason": "completed"})
    expect(issue["state"] == "closed" and issue["state_reason"] == "completed", f"#{issues[0]} is done")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zuul", required=True, help="zuul-web's URL")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--repository", required=True, help="the test bed's copy of AIDepartment, owner/name")
    parser.add_argument("--config-repository", required=True, help="its copy of kristofk/aid-zuul-config, owner/name")
    parser.add_argument("--checkout", required=True, help="a checkout of it, that can push")
    parser.add_argument("--secret", default="", help="the fixture secret sync.sh encrypted; none skips checks 12 and 13")
    parser.add_argument("--project-key", help="the test bed's project key for the config copy, sync.sh's")
    parser.add_argument("--native-launcher", action="store_true",
                        help="run layout, successful dispatch and unknown-label checks only")
    parser.add_argument("--loop-pull", type=int,
                        help="the builder's pull request: run the two-agent loop's check (14) alone, on it")
    args = parser.parse_args()
    if args.loop_pull and args.native_launcher:
        parser.error("--loop-pull and --native-launcher are mutually exclusive")
    bed = Testbed(args)
    if args.native_launcher:
        checks = [layout_loads, new_job_runs_speculatively, unknown_label_is_refused]
    elif args.loop_pull:
        checks = [layout_loads, lambda bed: two_agent_loop(bed, args.loop_pull)]
    else:
        checks = [layout_loads, new_job_runs_speculatively, unknown_label_is_refused, untrusted_base_job_is_refused,
                  untrusted_pipeline_is_refused, external_job_cannot_read_farm, job_timeout, ready_means_queued,
                  hold, speculative_queue, new_head_comes_back]
        if args.secret:
            checks += [post_job_gets_secret, pull_request_cannot_have_secret]
        else:
            log("Checks 12 and 13 are left out: the aid-zuul-config checkout has no secrets guard (sync.sh).")
    try:
        for check in checks:
            check(bed)
    except Failure as error:
        log(f"FAILED: {error}")
        return 1
    finally:
        bed.cleanup()
    log(f"All {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
