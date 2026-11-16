"""Exercise the pinned Zuul driver's real rebase merge against GitHub (#1656).

Run in the scheduler image with GITHUB_TOKEN and TESTBED_REPOSITORY. This creates
disposable issues and a pull request in a *-testbed repository. With --closer it
runs the trusted post-merge helper after the merge, then checks its result and
idempotence. Without it, it observes native GitHub behavior, reproducing #1656.
The workflow supplies Issues: write, as the production aid-release token does.
"""

import base64
import os
import argparse
from pathlib import Path
import runpy
import uuid

from zuul.driver.github import GithubDriver
from zuul.driver.github.githubconnection import GithubConnection

scenario = runpy.run_path(str(Path(__file__).with_name("scenario.py")))
GitHub, expect, wait, log = (scenario[name] for name in ("GitHub", "expect", "wait", "log"))


def check(closer):
    repository = os.environ["TESTBED_REPOSITORY"]
    if not repository.endswith("-testbed") or repository.count("/") != 1:
        raise ValueError("This destructive fixture runs only in an owner/*-testbed repository")
    token = os.environ["GITHUB_TOKEN"]
    github = GitHub(repository, token)
    connection = GithubConnection(GithubDriver(), "github", {
        "api_token": token, "rate_limit_logging": "false",
    })
    branch = f"issue-closing/{uuid.uuid4().hex}"
    issues, pull, created_branch = [], None, False
    try:
        repo = github.repo()
        base = repo["default_branch"]
        old = github.repo(f"/git/ref/heads/{base}")["object"]["sha"]
        for name in ("closing keyword", "ordinary reference"):
            issue = github.repo("/issues", method="POST", body={
                "title": f"Issue-closing probe: {name}", "body": "Disposable fixture for AIDepartment#1656.",
            })
            issues.append(issue["number"])
        github.repo("/git/refs", method="POST", body={"ref": f"refs/heads/{branch}", "sha": old})
        created_branch = True
        commit = github.repo(f"/contents/testbed/issue-closing/{branch.split('/')[-1]}.txt", method="PUT", body={
            "message": "Issue-closing probe: rebase merge", "branch": branch,
            "content": base64.b64encode(b"Rebase merge must close the linked issue.\n").decode(),
        })["commit"]["sha"]
        pull = github.repo("/pulls", method="POST", body={
            "head": branch, "base": base, "title": "Issue-closing probe: rebase merge",
            "body": f"Closes #{issues[0]}\n\nReferences #{issues[1]}",
        })
        log(f"Probe: {pull['html_url']}; closes #{issues[0]}, references #{issues[1]}")
        for number in issues:
            expect(github.repo(f"/issues/{number}")["state"] == "open", f"#{number} stays open before merge")
        wait("GitHub's mergeability calculation", lambda: github.repo(f"/pulls/{pull['number']}")["mergeable"], 60)
        merged_sha = connection.mergePull(repository, pull["number"], sha=commit, method="rebase")
        merged = github.repo(f"/pulls/{pull['number']}")
        expect(merged["merged"] and merged["merge_commit_sha"] == merged_sha,
               f"Zuul merged by rebase as {merged['merged_by']['login']}: {merged_sha}")
        parents = github.repo(f"/commits/{merged_sha}")["parents"]
        expect([parent["sha"] for parent in parents] == [old], "rebase keeps main linear")
        if closer:
            trusted = runpy.run_path(closer)
            result = trusted["close"](trusted["GitHub"](repository, token), old, merged_sha, base,
                                      merger="github-actions[bot]")
            log(f"Trusted post-merge closer: {result}")
            repeated = trusted["close"](trusted["GitHub"](repository, token), old, merged_sha, base,
                                        merger="github-actions[bot]")
            expect(not repeated["closed"], "repeating the post-merge job closes nothing twice")
        closed = wait(f"GitHub to close #{issues[0]} after Zuul's rebase merge", lambda: next(
            (issue for issue in [github.repo(f"/issues/{issues[0]}")]
             if issue["state"] == "closed" and issue["state_reason"] == "completed"), None), 90, interval=3)
        expect(closed["state_reason"] == "completed", f"#{issues[0]} closed as completed")
        expect(github.repo(f"/issues/{issues[1]}")["state"] == "open",
               f"ordinary reference #{issues[1]} stays open")
    finally:
        # Cleanup happens after the assertion, including on failure; it cannot make a failed probe pass.
        if pull and github.repo(f"/pulls/{pull['number']}")["state"] == "open":
            github.repo(f"/pulls/{pull['number']}", method="PATCH", body={"state": "closed"})
        for number in issues:
            if github.repo(f"/issues/{number}")["state"] == "open":
                github.repo(f"/issues/{number}", method="PATCH", body={"state": "closed", "state_reason": "not_planned"})
        if created_branch:
            github.repo(f"/git/refs/heads/{branch}", method="DELETE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--closer", help="the trusted config checkout's playbooks/issues/close.py")
    check(parser.parse_args().closer)
