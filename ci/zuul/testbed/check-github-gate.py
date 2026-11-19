"""Check the original config project's pipelines with the installed Zuul driver (#1535).

Run in the pinned scheduler image, before sync.sh's workflow-token adaptations:
  python3 check-github-gate.py /path/to/original-pipelines.yaml
No GitHub calls: PullRequest.reviews uses the driver's normalized review records.
The Compose scenario separately proves the layout loads with config-errors == [].
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import voluptuous as vs
import yaml
from zuul.configloader import ParseContext
from zuul.driver.github import githubreporter, githubsource, githubtrigger
from zuul.driver.github.githubmodel import GithubRefFilter, GithubTriggerEvent, PullRequest

# The reports that end an item, and the check run states that complete GitHub's check run (14.3.0's
# GithubReporter.updateCheck; success and failure complete it with the buildset's result).
ENDS = ("success", "failure", "dequeue", "merge-conflict", "config-error", "no-jobs")
COMPLETES = {"success", "failure", "cancelled", "skipped", "neutral"}


def check_runs_end(pipelines):
    """A check run a pipeline opens, each way out of it completes (#1660).

    Zuul reports enqueue before it knows an item's jobs, and start only for an item that has some (14.3.0's
    PipelineManager._processOneItem). So a pipeline that opens its check run at enqueue, as gate does, must complete it
    on no-jobs too: aid-zuul-config's approved pull requests enter gate, which runs nothing for them.
    """
    reporter = vs.Schema(githubreporter.getSchema())
    for pipeline in pipelines:
        name = pipeline["name"]
        reports = {action: pipeline.get(action, {}).get("github", {}) for action in ("enqueue", "start") + ENDS}
        for config in reports.values():
            reporter(config)
        opened = [action for action in ("enqueue", "start") if reports[action].get("check")]
        if not opened:
            continue
        for end in ENDS if "enqueue" in opened else ENDS[:-1]:
            state = reports[end].get("check")
            assert state in COMPLETES, (f"{name}: {end} leaves the check run that {opened[0]} opened at "
                                        f"{reports[opened[0]]['check']} ({state})")
        print(f"{name}: the check run {'/'.join(opened)} opens, {'/'.join(ENDS if 'enqueue' in opened else ENDS[:-1])} "
              "completes")


def check(path):
    # The pipelines GitHub starts: periodic-0200 and periodic-0300 are the timer's (#1350), and have no GitHub trigger.
    pipelines = [item["pipeline"] for item in yaml.safe_load(Path(path).read_text())
                 if "github" in item["pipeline"].get("trigger", {})]
    context = ParseContext(SimpleNamespace(connections={}), None, None, None)
    trigger = githubtrigger.GithubTrigger(None, {})
    filters = {}
    for pipeline in pipelines:
        config = pipeline["trigger"]["github"]
        # getSchema is still permissive in 14.3.0; getEventFilters also checks
        # getNewSchema and records precisely the warning seen in config-errors.
        filters[pipeline["name"]] = trigger.getEventFilters("github", config, context)
        for key, schema in (("require", githubsource.getRequireSchema),
                            ("reject", githubsource.getRejectSchema)):
            vs.Schema(schema())(pipeline.get(key, {}).get("github", {}))
    assert not context.error_list, [error.error for error in context.error_list]
    print("Original pipelines: GitHub trigger/require/reject schemas loaded without warnings")
    check_runs_end(pipelines)

    gate = next(pipeline for pipeline in pipelines if pipeline["name"] == "gate")
    required = GithubRefFilter.requiresFromConfig("github", gate["require"]["github"])
    rejected = GithubRefFilter.rejectFromConfig("github", gate["reject"]["github"])
    change = PullRequest(None)
    change.number = 1
    change.open = True
    change.is_current_patchset = True
    change.draft = False
    change.contexts = {("aid-zuul", "aid/check", "success")}

    def review(permission, state="approved"):
        return {"by": {"username": "reviewer-" + permission},
                "type": state, "permission": permission}

    # The same ready, current head with successful check: only reviews vary.
    # Test every enqueue trigger, including a privileged ready/regate event,
    # to prove the pipeline requirement protects all routes into gate.
    for config, event_filter in zip(gate["trigger"]["github"], filters["gate"]):
        event = GithubTriggerEvent()
        event.connection_name = "github"
        event.type = config["event"]
        event.action = config["action"]
        event.permission = "write"
        event.state = "approved"
        event.check_run = "aid-zuul:aid/check:success"
        event.label = "hold"
        event.comment = "regate"
        assert event_filter.matches(event, change), config

        def admitted(reviews):
            change.reviews = reviews
            return bool(event_filter.matches(event, change)
                        and required.matches(change) and rejected.matches(change))

        route = f"{event.type}/{event.action}"
        assert not admitted([]), f"{route}: no approval entered gate"
        assert not admitted([review("read")]), f"{route}: read-only approval entered gate"
        assert not admitted([review("write", "commented")]), f"{route}: comment entered gate"
        for permission in ("write", "admin"):
            assert admitted([review(permission)]), f"{route}: {permission} approval refused"
            assert admitted([review(permission), review("read", "changes_requested")]), route
        for permission in ("write", "admin"):
            assert not admitted([review("write"), review(permission, "changes_requested")]), route
        print(f"Gate {route}: no/read-only/comment approval refused; write/admin accepted; "
              "write/admin changes-requested blocks, read-only does not")


if __name__ == "__main__":
    check(sys.argv[1])
