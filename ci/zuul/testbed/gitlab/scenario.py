"""The GitLab test bed's two halves (../../forge.md, "The check on GitLab"), which run.sh runs:

  scenario.py setup --aid <dir> --config <dir> --zuul-token-file <file>
      Before Zuul starts: GitLab's users (builder, reviewer and zuul), the group aid, its two projects with the copies
      pushed to main (aid/aidepartment and aid/aid-zuul-config), their labels and their webhooks to zuul-web. Writes
      zuul's API token to the file, for the connection (zuul.conf).

  scenario.py run --zuul http://127.0.0.1:9000 --results <file>
      Against the running Zuul: the layout loads from GitLab, then merge requests go through check and gate as the
      two-agent loop's would. What must hold fails the run; what GitLab does that GitHub does not is recorded in the
      results file, which the forge document cites.

  1. The layout loads: both projects, from GitLab, with no configuration error.
  2. The builder's merge request passes check: Zuul runs check's jobs on it and labels it check-passed.
  3. Ready alone (the builder's label) leaves it out of gate: it is not approved.
  4. The reviewer's approval lets it in: gate runs its full job on it, and Zuul merges it.
  5. hold keeps a ready, approved merge request out of gate. Then the builder pushes, check passes again, and hold
     comes off: whether the reviewer's approval of the older head still lets it in is recorded, not required.

Standard library only. Reads GITLAB_URL (default http://127.0.0.1:8080), GITLAB_TOKEN (root's, with the sudo scope),
ZUUL_WEBHOOK_TOKEN and, for setup, GITLAB_HOOK_URL (zuul-web's payload route as GitLab reaches it).
"""

import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "aid/aidepartment"
CONFIG = "aid/aid-zuul-config"
LABELS = {"check-passed": "#1aaa55", "ready": "#428bca", "hold": "#d9534f"}


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
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        return 0, str(error)


def wait(what, check, timeout, every=10):
    deadline = time.time() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.time() > deadline:
            raise Failure(f"timed out after {timeout} s waiting for {what}")
        time.sleep(every)


class GitLab:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def call(self, method, path, body=None, sudo=None, ok=(200, 201, 202, 204)):
        headers = {"PRIVATE-TOKEN": self.token}
        if sudo:
            headers["Sudo"] = sudo
        status, data = request(method, f"{self.url}/api/v4{path}", body, headers)
        if status not in ok:
            raise Failure(f"GitLab {method} {path}{f' as {sudo}' if sudo else ''}: {status} {data}")
        return data

    @staticmethod
    def project(name):
        return f"/projects/{urllib.parse.quote(name, safe='')}"

    def mr(self, iid, path="", **kwargs):
        method = kwargs.pop("method", "GET")
        return self.call(method, f"{self.project(PROJECT)}/merge_requests/{iid}{path}", **kwargs)


class Zuul:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def get(self, path):
        status, data = request("GET", f"{self.url}/api/tenant/aid{path}")
        return data if status == 200 else None

    def buildsets(self, pipeline, iid, patchset=None):
        fields = {"project": PROJECT, "pipeline": pipeline, "change": iid}
        if patchset:
            fields["patchset"] = patchset
        query = urllib.parse.urlencode(fields)
        return self.get(f"/buildsets?{query}") or []

    def queued(self, pipeline, iid):
        """Whether the merge request is an item of the pipeline now, from Zuul's status."""
        status = self.get("/status") or {}
        for entry in status.get("pipelines", []):
            if entry.get("name") != pipeline:
                continue
            for queue in entry.get("change_queues", []):
                for head in queue.get("heads", []):
                    for item in head:
                        for change in item.get("changes", [item]):
                            if str(change.get("id", "")).split(",")[0] == str(iid) and change.get("project") == PROJECT:
                                return True
        return False


def push(gitlab, directory, project):
    """The copy in directory, as one commit on main of the project."""
    remote = urllib.parse.urlsplit(gitlab.url)
    url = f"{remote.scheme}://root:{gitlab.token}@{remote.netloc}/{project}.git"
    git = ["git", "-C", directory, "-c", "user.name=Test bed", "-c", "user.email=testbed@gitlab.invalid"]
    subprocess.run(git[:3] + ["init", "-q", "-b", "main"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", f"The GitLab test bed's copy of {project}"], check=True)
    subprocess.run(git + ["push", "-q", url, "HEAD:main"], check=True)


def setup(args):
    gitlab = GitLab(os.environ.get("GITLAB_URL", "http://127.0.0.1:8080"), os.environ["GITLAB_TOKEN"])
    hook_url = os.environ["GITLAB_HOOK_URL"]
    webhook_token = os.environ["ZUUL_WEBHOOK_TOKEN"]
    wait("GitLab's API", lambda: request("GET", f"{gitlab.url}/api/v4/version",
                                         headers={"PRIVATE-TOKEN": gitlab.token})[0] == 200, 600)
    # zuul-web is on Compose's network, which GitLab refuses for webhooks unless allowed. No Auto DevOps: GitLab CI
    # is not what is tested, and its pipelines would wait for runners there are none of.
    gitlab.call("PUT", "/application/settings", {
        "allow_local_requests_from_web_hooks_and_services": True,
        "auto_devops_enabled": False,
        "signup_enabled": False,
    })
    users = {}
    for name in ("builder", "reviewer", "zuul"):
        users[name] = gitlab.call("POST", "/users", {
            "username": name, "name": name, "email": f"{name}@gitlab.invalid",
            "password": secrets.token_urlsafe(24), "skip_confirmation": True,
        })["id"]
    expires = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    token = gitlab.call("POST", f"/users/{users['zuul']}/impersonation_tokens",
                        {"name": "zuul", "scopes": ["api"], "expires_at": expires})["token"]
    with open(args.zuul_token_file, "w") as handle:
        handle.write(token)
    group = gitlab.call("POST", "/groups", {"name": "aid", "path": "aid", "visibility": "private"})["id"]
    members = {PROJECT: {"builder": 30, "reviewer": 30, "zuul": 40}, CONFIG: {"zuul": 40}}
    for project, directory in ((CONFIG, args.config), (PROJECT, args.aid)):
        created = gitlab.call("POST", "/projects", {
            "name": project.split("/")[1], "path": project.split("/")[1], "namespace_id": group,
            "visibility": "private", "merge_method": "merge", "builds_access_level": "disabled",
            "only_allow_merge_if_pipeline_succeeds": False,
        })
        for user, level in members[project].items():
            gitlab.call("POST", f"/projects/{created['id']}/members", {"user_id": users[user], "access_level": level})
        push(gitlab, directory, project)
        # main protected, Maintainers alone pushing and merging, which exclude-unprotected-branches needs. GitLab 19
        # does not protect it on the first push.
        branch = gitlab.call("GET", f"{gitlab.project(project)}/repository/branches/main")
        if not branch.get("protected"):
            gitlab.call("POST", f"{gitlab.project(project)}/protected_branches",
                        {"name": "main", "push_access_level": 40, "merge_access_level": 40})
        if not gitlab.call("GET", f"{gitlab.project(project)}/repository/branches/main").get("protected"):
            raise Failure(f"{project}: main is not protected")
        if project == PROJECT:
            for label, color in LABELS.items():
                gitlab.call("POST", f"{gitlab.project(project)}/labels", {"name": label, "color": color})
        # After the push, so that GitLab sends nothing to a Zuul that is not up yet (it disables a hook that keeps
        # failing).
        gitlab.call("POST", f"{gitlab.project(project)}/hooks", {
            "url": hook_url, "token": webhook_token, "push_events": True, "merge_requests_events": True,
            "note_events": True, "enable_ssl_verification": False,
        })
        log(f"{project}: made, pushed, protected, with its webhook")


class Run:
    def __init__(self, args):
        self.gitlab = GitLab(os.environ.get("GITLAB_URL", "http://127.0.0.1:8080"), os.environ["GITLAB_TOKEN"])
        self.zuul = Zuul(args.zuul)
        self.results = {"checks": [], "observations": {}}
        self.results_file = args.results

    def passed(self, number, what):
        log(f"PASS {number}: {what}")
        self.results["checks"].append({"check": number, "passed": what})

    def observe(self, key, value, what):
        log(f"SEEN {key} = {json.dumps(value)}: {what}")
        self.results["observations"][key] = {"value": value, "what": what}

    def save(self):
        with open(self.results_file, "w") as handle:
            json.dump(self.results, handle, indent=2)

    def open_mr(self, name, labels=()):
        project = self.gitlab.project(PROJECT)
        self.gitlab.call("POST", f"{project}/repository/commits", {
            "branch": name, "start_branch": "main", "commit_message": f"Forge check: {name}",
            "actions": [{"action": "create", "file_path": f"testbed/gate/{name}.txt", "content": f"# {name}\n"}],
        }, sudo="builder")
        mr = self.gitlab.call("POST", f"{project}/merge_requests", {
            "source_branch": name, "target_branch": "main", "title": f"Forge check: {name}",
            "description": "A change built and reviewed as the two-agent loop's would be.", "labels": ",".join(labels),
        }, sudo="builder")
        log(f"!{mr['iid']}: opened by builder, {mr['web_url']}")
        return mr["iid"]

    def checked(self, iid, sha=None):
        """check passed on the merge request's head (sha, if given): its buildset succeeded and the label is on."""
        def done():
            mr = self.gitlab.mr(iid)
            if sha and mr["sha"] != sha:
                return None
            sets = self.zuul.buildsets("check", iid, mr["sha"])
            if any(s.get("result") not in (None, "SUCCESS") for s in sets):
                raise Failure(f"!{iid}: check failed on {mr['sha']}: {sets}")
            return mr if "check-passed" in mr["labels"] and any(s.get("result") == "SUCCESS" for s in sets) else None
        return wait(f"!{iid}'s check", done, 1500)

    def stays_out(self, iid, why, seconds=60):
        """Nothing lets it into gate for a minute: it is in no gate item, has no gate buildset and is not merged."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.zuul.queued("gate", iid) or self.zuul.buildsets("gate", iid):
                raise Failure(f"!{iid} entered gate, though {why}")
            if self.gitlab.mr(iid)["state"] != "opened":
                raise Failure(f"!{iid} was merged or closed, though {why}")
            time.sleep(5)

    def label(self, iid, add=(), remove=()):
        self.gitlab.mr(iid, method="PUT", body={"add_labels": ",".join(add), "remove_labels": ",".join(remove)},
                       sudo="builder")

    def run(self):
        # 1.
        def loaded():
            projects = self.zuul.get("/projects") or []
            names = {p.get("name") for p in projects}
            errors = self.zuul.get("/config-errors")
            return errors is not None and {PROJECT, CONFIG} <= names and (errors or "none")
        errors = wait("the tenant to load from GitLab", loaded, 900)
        if errors != "none":
            raise Failure(f"the layout has configuration errors: {json.dumps(errors)[:4000]}")
        pipelines = sorted(p["name"] for p in self.zuul.get("/pipelines") or [])
        self.passed(1, f"the tenant loaded both projects from GitLab, with no configuration error; pipelines {pipelines}")

        # 2.
        one = self.open_mr("forge-1")
        mr = self.checked(one)
        jobs = sorted({b["job_name"] for s in self.zuul.buildsets("check", one) for b in
                       (self.zuul.get(f"/buildset/{s['uuid']}") or {}).get("builds", [])})
        self.passed(2, f"!{one} passed check on {mr['sha'][:12]}, jobs {jobs}, labelled check-passed")
        approvals = self.gitlab.mr(one, "/approvals", sudo="builder")
        self.observe("author_can_approve", approvals.get("user_can_approve"),
                     "GitLab's approvals of the builder's merge request, read as the builder: may it approve its own")

        # 3.
        self.label(one, add=["ready"])
        self.stays_out(one, "it is ready but not approved")
        self.passed(3, f"!{one}: ready (the label) and checked, but not approved, stayed out of gate")

        # 4.
        self.gitlab.mr(one, "/approve", method="POST", body={"sha": mr["sha"]}, sudo="reviewer")
        log(f"!{one}: approved by reviewer at {mr['sha'][:12]}")
        merged = wait(f"!{one} to merge", lambda: (lambda m: m if m["state"] == "merged" else None)(self.gitlab.mr(one)),
                      1800)
        gate = self.zuul.buildsets("gate", one)
        if not any(s.get("result") == "SUCCESS" for s in gate):
            raise Failure(f"!{one} merged without a successful gate buildset: {gate}")
        self.gitlab.call("GET", f"{self.gitlab.project(PROJECT)}/repository/files/"
                         f"{urllib.parse.quote('testbed/gate/forge-1.txt', safe='')}?ref=main")
        jobs = sorted({b["job_name"] for s in gate for b in (self.zuul.get(f"/buildset/{s['uuid']}") or {}).get("builds", [])})
        self.passed(4, f"!{one}: the reviewer's approval let it into gate, which ran {jobs}, and Zuul merged it "
                       f"({merged['merge_commit_sha'] and merged['merge_commit_sha'][:12]})")
        main = self.gitlab.call("GET", f"{self.gitlab.project(PROJECT)}/repository/commits/main")
        self.observe("merge_commit_parents", len(main["parent_ids"]),
                     "parents of main's head after Zuul merged: 2 is a merge commit, so main is not linear")
        self.save()

        # 5.
        two = self.open_mr("forge-2", labels=["hold"])
        first = self.checked(two)
        self.gitlab.mr(two, "/approve", method="POST", body={"sha": first["sha"]}, sudo="reviewer")
        self.label(two, add=["ready"])
        self.stays_out(two, "it is labelled hold")
        self.passed(5, f"!{two}: hold kept it out of gate, ready, checked and approved")
        project = self.gitlab.project(PROJECT)
        pushed = self.gitlab.call("POST", f"{project}/repository/commits", {
            "branch": "forge-2", "commit_message": "Forge check: a push after the approval",
            "actions": [{"action": "update", "file_path": "testbed/gate/forge-2.txt", "content": "# forge-2, pushed\n"}],
        }, sudo="builder")["id"]
        second = self.checked(two, sha=pushed)
        approvals = self.gitlab.mr(two, "/approvals")
        self.observe("approved_after_push", approvals.get("approved"),
                     "GitLab's approved after the builder pushed over the head the reviewer approved")
        self.observe("approved_by_after_push", [a["user"]["username"] for a in approvals.get("approved_by", [])],
                     "who GitLab still counts as having approved, after that push")
        self.label(two, remove=["hold"])
        try:
            wait(f"!{two} to merge", lambda: self.gitlab.mr(two)["state"] == "merged", 900)
            stale = True
        except Failure:
            stale = False
            self.gitlab.mr(two, method="PUT", body={"state_event": "close"})
        self.observe("merged_on_older_approval", stale,
                     f"with hold off, gate merged !{two} at {second['sha'][:12]}, on an approval of {first['sha'][:12]}"
                     if stale else f"with hold off, !{two} stayed out: the push took the approval away")
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("setup")
    one.add_argument("--aid", required=True)
    one.add_argument("--config", required=True)
    one.add_argument("--zuul-token-file", required=True)
    two = sub.add_parser("run")
    two.add_argument("--zuul", required=True)
    two.add_argument("--results", required=True)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup(args)
        else:
            run = Run(args)
            try:
                run.run()
            finally:
                run.save()
    except Failure as failure:
        log(f"FAIL: {failure}")
        sys.exit(1)


if __name__ == "__main__":
    main()
