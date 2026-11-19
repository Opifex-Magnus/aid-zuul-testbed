"""Exercise the pinned Zuul driver's real rebase merge against GitHub (#1656).

Run in the scheduler image (or an environment with Zuul 14.3.0 installed) with
GITHUB_TOKEN and TESTBED_REPOSITORY. The token is supplied by the caller: a workflow
token, an account token, or an installation token for this disposable repository.
It creates fixtures and observes native closure without a helper or issue PATCH
before the assertion. An Actions-token result does not establish another App's
behavior; record the actual merged_by principal with the outcome.

With the credential already in GITHUB_TOKEN and Zuul 14.3.0 installed, run:
  TESTBED_REPOSITORY=owner/repository-testbed python3 -I -B ci/zuul/testbed/issue_closing.py
The Actions token is known to fail native closure; a separately supplied account
or installation token can test that principal. This diagnostic is not part of the
Compose scenario and does not assert that every token type behaves alike.
"""

import base64
import os
from pathlib import Path
import runpy
import uuid

from zuul.driver.github import GithubDriver
from zuul.driver.github.githubconnection import GithubConnection

scenario = runpy.run_path(str(Path(__file__).with_name("scenario.py")))
GitHub, expect, wait, log = (scenario[name] for name in ("GitHub", "expect", "wait", "log"))


def check():
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
        owner, name = repository.split("/")
        query = """query($owner:String!, $name:String!, $number:Int!) {
          repository(owner:$owner, name:$name) {
            pullRequest(number:$number) { closingIssuesReferences(first:100) { nodes { number } } }
          }
        }"""
        def closing_links():
            result = github.call("POST", "/graphql", {
                "query": query, "variables": {"owner": owner, "name": name, "number": pull["number"]},
            })
            return [node["number"] for node in result["data"]["repository"]["pullRequest"]
                    ["closingIssuesReferences"]["nodes"]]
        links = wait("GitHub to register the closing link before merging",
                     lambda: (links if issues[0] in (links := closing_links()) else None), 60)
        expect(issues[1] not in links, f"closingIssuesReferences before merge: {links}")
        wait("GitHub's mergeability calculation", lambda: github.repo(f"/pulls/{pull['number']}")["mergeable"], 60)
        merged_sha = connection.mergePull(repository, pull["number"], sha=commit, method="rebase")
        merged = github.repo(f"/pulls/{pull['number']}")
        expect(merged["merged"] and merged["merge_commit_sha"] == merged_sha,
               f"Zuul merged by rebase as {merged['merged_by']['login']}: {merged_sha}")
        parents = github.repo(f"/commits/{merged_sha}")["parents"]
        expect([parent["sha"] for parent in parents] == [old], "rebase keeps main linear")
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
    check()
