# Zuul's configuration

What Zuul runs for AID (#1341, #1346), in two repositories:

- **[kristofk/aid-zuul-config](https://github.com/kristofk/aid-zuul-config)**, the config project (trusted): the
  pipelines, the base job, and the jobs with secrets and their secrets
  ([`deploy/zuul/job-secrets.md`](../../deploy/zuul/job-secrets.md), #1351).
- **This folder**, AID's own project (untrusted): the job library, AID's jobs, and which pipeline runs which.

Zuul's deployment is [`deploy/zuul/`](../../deploy/zuul/README.md). The cutover is held until
the orchestrator completes [cutover.md](cutover.md); it records every live prerequisite and
runner retirement. After that switch, Zuul alone gates main and publishes from it.

| Path | What | A pull request's change to it |
| --- | --- | --- |
| aid-zuul-config's `zuul.d/` and `playbooks/` | the pipelines, the base job and its playbooks | takes effect once merged |
| [`zuul.d/`](zuul.d) | the job library and AID's jobs, and which pipeline runs which | runs on the pull request itself |
| [`playbooks/`](playbooks) | the library's playbooks and their roles | runs on the pull request itself |
| [`testbed/`](testbed) | Zuul on both, in Docker Compose, with checks | |
| [`disposable-nodes.md`](disposable-nodes.md) | fresh pods per job, fixed-namespace Kubernetes driver, caches and coordinated rollout (#1698) | |
| [`gate.md`](gate.md) | how `check`, `gate` and `hold` admit, test and merge reviewed changes | |
| [`comparison.md`](comparison.md) | historical ordinary-run comparison with the prior CI, reporting times and remaining coverage/rollout gaps (#1353) | |
| [`publish.md`](publish.md) | `post` and the periodic pipelines: the app images, AID's release, the release index and the nightly on Zuul, which CI publishes (`ci/publisher`), and turning it on (#1350) | |
| [`forge.md`](forge.md) | what moving from GitHub to another forge would touch, with Zuul staying, and one merge request through check and gate on GitLab (#1356) | |
| [`testbed/gitlab/`](testbed/gitlab) | Zuul on GitLab CE, in Docker Compose: the pipelines' GitLab form and that merge request ([forge.md](forge.md), "The check on GitLab") | |
| [`publish/`](publish) | the scripts those jobs run: aid-zuul-config's publishing jobs, and the nightly | runs on the pull request's own jobs only where one names it (`aid-nightly`); aid-zuul-config's jobs run `main`'s |

## Trusted and untrusted

Zuul splits its configuration in two ([Zuul's docs](https://zuul-ci.org/docs/zuul/latest/tenants.html)), and AID keeps
the halves in separate repositories (Kristóf, on #1346): the trusted half is the security boundary, since what is in it
decides what every job may do and see.

- **The config project is trusted.** Only it may define pipelines, a job with no parent (the base job) and the
  providers of build nodes. Its playbooks run outside the sandbox that untrusted playbooks get, and see the secrets it
  defines. So Zuul never runs a proposed change to it: a pull request there is checked for configuration errors, and
  takes effect once it is merged. It has merge controls of its own: `main` is protected, a change is a pull request
  reviewed by the other bot, and only the orchestrator merges.
- **AID's project is untrusted.** Its jobs, playbooks and roles are tested *speculatively*: a pull request that adds
  or changes a job runs that job, as changed, on itself, merged onto `main` and onto the pull requests ahead of it in
  the gate. What it may not do, Zuul refuses with a configuration error on the pull request: a pipeline, or a base job
  (the test bed checks both). The trusted base currently rejects `check-external` before untrusted code starts,
  until separate credential-free nodes exist; the PR node's raw farm token is readable by its job user.

AID's configuration is in `ci/zuul/zuul.d/`, which the tenant names (`extra-config-paths`), so that all of CI stays
under `ci/`. Zuul would also read a `zuul.yaml`, `zuul.d/`, `.zuul.yaml` or `.zuul.d/` at the repository's root, so
[`check.mjs`](check.mjs) refuses them: AID's jobs have one place.

## The job library

The base job checks the code out; AID's jobs build and test with Bazel, or run a script from the repository. Nothing
in Ansible holds build logic, so that a step runs the same outside Zuul, and Zuul could be replaced without rewriting
the steps (#1341):

- **`base`** (aid-zuul-config's `zuul.d/jobs.yaml`), every job's parent: one build node of the label `aid-check-pod-node`
  (#1345), a 2-hour timeout, and a pre-run that starts the console stream (`start-zuul-console`) and puts every project
  of the job, as Zuul prepared it, into `~/src/<host>/<owner>/<repository>` (`prepare-workspace-git`). Both roles are
  Zuul's own, from `zuul/zuul-jobs`.
- **`aid-bazel`** ([`zuul.d/jobs.yaml`](zuul.d/jobs.yaml)), abstract: `bazel <bazel_startup_flags> <bazel_command> <bazel_flags> --
  <bazel_targets>` in the checkout, with the node's Bazelisk, which runs the version `.bazelversion` pins.
- **`aid-script`**, abstract: `./<script> <script_args>` in the checkout, where `script` is a path under `ci/` or
  `scripts/`. The role refuses any other.
- **AID's jobs** set those variables, and nothing else that runs. `check` runs the cheap ones, the plan job's script
  checks of CI's cheap layer (`aid-seam-lint`, `aid-seam-lint-test`, `aid-apps-lint`, `aid-apps-lint-test`) plus
  `aid-cheap-checks` (quarantine validation, CI/workflow policy, legacy capture and affected Node suites), and
  `gate` CI's queue run, each step a job of `aid-gate` (`./ci/zuul/gate.sh`) or the Bazel build and test
  (`aid-bazel-everything`), those the plan of `aid-gate-plan` names: [gate.md](gate.md), "The gate's jobs".
  `periodic-0200` and `periodic-0300` run the nightly, `aid-nightly`; what publishes from `main` is aid-zuul-config's,
  since each of those jobs holds a secret ([publish.md](publish.md)). The [comparison](comparison.md) records the
  cheap and gate coverage gaps of 2026-11-15, which #1520 (check's) and #1521 (gate's, these jobs) close.

Check and gate's non-image jobs share three disposable `aid-check-pod-node` slots in `zuul-nodes`.
The namespace quota and the trusted `aid-k3s-check-pods` provider's `resource-limits: {pods: 3}`
must agree. The orchestrator updates that private provider after the quota change merges and the
step 3 host and farm ramp (#1885) is applied. Gate image and post providers retain one slot each.
#1867 will give gate non-image jobs a separate pod; its current proposed 1 GiB / 100m CPU request
would bring the total to 18656/18048 MiB and 8330/9000m CPU. Step 3 leaves 416 MiB. The
[orchestrator's #1885 scope decision](https://github.com/kristofk/AIDepartment/issues/1885#decided-by-the-orchestrator-2026-11-19)
excludes that pod from this ramp: #1867 sizes it from post-step-3 measurements and resolves the
608 MiB gap in its own change. The current gate estimate and allocation stay unchanged;
the rendered-budget regression rejects adding that pod without a budget change.

## Executor resources

Set CPU and memory on each executor's pod/nodeset, in `deploy/zuul/` and the trusted
`aid-zuul-config` provider definitions. Build scripts carry no node-specific jobs budget (#1695).
Bazel's `HOST_CPUS` and `HOST_RAM` honour the node's cgroup, including for local work on a remote
build; the farm retains its own 200-job queue and worker slot limits. The release folder's clean
local build reads the post, image or staging node's `cpu.max` and `memory.max`, using the same
67% RAM fraction as Bazel. See [Build resource limits](../../docs/development.md#build-resource-limits).

## The cheap checks

`aid-cheap-checks` calls [`cheap.mjs`](cheap.mjs) with `zuul.branch`, beside the four lint jobs in both `check` and
`check-external`. It first runs:

```sh
bazel test --config=ci --config=zuul -- //:node_quarantine_check //:node_ci //:node_legacy
```

These are Bazel test actions on the farm, with the pinned Node runtime and the existing quarantine wrapper.
The expiry check is uncached because its answer depends on today's date. CI test files have individual targets
`//:node_ci_<file stem>`; `//:node_ci` groups all of them.

A failed command fails the voting job before suites run. Then `ci/affected.mjs` plans the difference between
`refs/remotes/origin/<target-branch>` and `HEAD`. Zuul preserves that remote ref as the state before the queue item's
speculative merge ([Zuul's Git repositories](https://zuul-ci.org/docs/zuul/latest/job-content.html#git-repositories));
it covers every commit of a rebased pull request without fetching a newer base. If the diff cannot be read, every
Node suite runs. Unknown paths use the planner's existing full fallback.

Only the plan's Node suites run, as `bazel test --config=ci --config=zuul -- //:node_<suite> ...`.
The commands inside those actions come from [`../node-suites.json`](../node-suites.json), shared with the local
helper. Each adapter target preserves its extraction, adapter tests and CI tests. Mock AID, lab and page script
have targets too. An unknown suite fails closed; any failing target fails `aid/check`.

Bazel owns test caching. A second unchanged run reuses Node results, except the date-sensitive quarantine check.
Tests run from writable declared-input copies on the farm, with a private HOME and pinned Node on PATH.
`requires-loopback` targets need the farm workers' ephemeral loopback listeners. Their shell/git/Python/OpenSSL/C
fixtures use the workers' platform userland; no suite needs a live network service or Docker daemon.
Gate's `aid-bazel-everything` and the nightly include these targets through `//...`; the nightly disables test caching.
`aid-gate-quarantine-node` runs the manual, uncached `//:node_quarantine` action and copies its report from Bazel outputs.
No Node test executes on the PR node or builder box. The script/testbed checks exercise selection and error propagation
with fake Bazel commands; they never start another farm build from inside a test action.

## Adding or changing a job

An ordinary pull request here, tested by Zuul on itself:

1. Add the job to [`zuul.d/jobs.yaml`](zuul.d/jobs.yaml), with `parent: aid-bazel` or `parent: aid-script` and its
   variables. What it runs is a Bazel target or a script under `ci/` or `scripts/`, in the same pull request if it is
   new. Set `timeout` when the job needs much less than two hours, and `files` if only some changes should run it.
2. List it under the pipelines that should run it, in [`zuul.d/project.yaml`](zuul.d/project.yaml): `check` for every
   pull request, `gate` for what must pass before a merge.
3. Open the pull request. Zuul's `check` runs the new job on it, as the pull request defines it. A mistake in the
   configuration fails `check` with Zuul's error on the pull request. Fix it and push. For an unrelated failure on
   an unchanged, reviewed head, record the failure and use the authenticated
   [same-head check recovery](../../deploy/zuul/web.md#recovering-check-on-the-same-head); `recheck` is ignored.

A new playbook or role goes under [`playbooks/`](playbooks), and is tested the same way. Keep it to calling Bazel or a
script: a new kind of step is a script.

`(cd ci/zuul && node check.mjs)` checks the layout's rules before you push ([Checks](#checks)).

## Changing the pipelines or the base job

They are in kristofk/aid-zuul-config, whose README has the rules: never merge there yourself; a change is a pull
request, reviewed by the other bot and merged by the orchestrator.

1. Run the test bed with the branch ([The test bed](#the-test-bed)): it loads it as the config project, as ThunderBox
   would after the merge, and runs every check.
2. Open the pull request there, with the test bed's run linked. Zuul's `check` on it reports a configuration error, if
   any, before the merge. It must pass before a merge, and `main` there is to require it (#1526, `deploy/zuul/protect-config.sh`, pending): it is
   the running scheduler's own load, which also knows the connections ThunderBox has deployed. The test bed has its
   own `zuul.conf`, so a reporter on a connection that is only in this repository passes there and fails here.
3. After the merge, Zuul loads it from `main`. `curl <zuul-web>/api/tenant/aid/config-errors` is empty if it loaded.

A change that needs both repositories (a new pipeline and the jobs in it) is two pull requests. Merge the config
project's first: until it is merged, AID's jobs cannot name the new pipeline.

## The test bed

Before starting Compose, [`testbed/check-github-gate.py`](testbed/check-github-gate.py) runs in the pinned scheduler
image against the config checkout's **original** pipelines, saved by `sync.sh` before its workflow-token adaptations.
It uses Zuul's GitHub parser to reject schema warnings and its actual event/review filters to check every gate entry
route: a ready, current pull request with a successful check stays out with no approval, a read-only approval or a
comment review; write/admin approvals let it in. Write/admin requests for changes block it; read-only ones do not.
The permission check belongs on `require.github.review` / `reject.github.review`; Zuul 14.3.0's
[`pull_request_review` trigger schema](https://opendev.org/zuul/zuul/src/tag/14.3.0/zuul/driver/github/githubtrigger.py)
does not accept `permission` (#1535). This probe makes no API calls: its reviews are normalized driver records, so
it checks the permission filter separately from GitHub's permission lookup and the live scenarios below.

[`testbed/`](testbed) runs Zuul, with ThunderBox's images, ZooKeeper configuration and `zuul.conf`, on both halves, in
Docker Compose on a GitHub-hosted runner, and checks it with ordinary pull requests
([`testbed/scenario.py`](testbed/scenario.py)):

1. the layout loads: the copy of aid-zuul-config as the config project, and AID's project, with no configuration
   error;
2. a pull request that adds a job (an `aid-bazel` job building a new target) runs it on itself, and reports success
   on the pull request, while `main` still has no such job;
3. a job requesting an unknown label fails with Zuul's exact label-not-found configuration error and no
   dispatched builds; its proposed library uses equivalent cleanup post-runs so unrelated deprecations cannot
   hide the diagnostic (#1889);
4. a pull request that defines a base job in AID's project fails, with Zuul's "Base jobs may only be defined in
   config projects" on the pull request;
5. a pull request that defines a pipeline in AID's project fails, with Zuul's "Pipelines may not be defined in
   untrusted repos" on the pull request;
6. a proposed job reads a fake raw farm token in every pre/run/post/cleanup hook under `check`, then fails at the
   trusted base guard under `check-external`. The node's probe file remains unchanged, and the executor log must
   identify the guard failure. No external hook executes, including post and cleanup after the failed pre-run;
7. a job runs no longer than its timeout;
8. ready means queued: a draft stays out of gate; once ready, it enters, runs the full jobs and merges by rebase;
9. the `hold` label keeps a ready pull request out of gate, and takes one out that is already queued;
10. two pull requests run at once in gate's window; a flaky failure starts the one behind it again, and `regate`
    retries the failed pull request;
11. check's result alone brings a ready pull request into gate, before and after a push. Checks 7–11 are the rows
    of [gate.md](gate.md), "Seen working";
12. a job with a secret, defined in the config project as `deploy/zuul/job-secrets.md` says, gets it in `post` on
    `main`: its log and its build's artifact show the fixture's SHA-256, never the value. One whose secret may not go
    to `post` is failed by aid-zuul-config's guard, `playbooks/secrets/guard.yaml`, before its run playbook. A job
    with an OIDC secret gets a token Zuul signed, with the claims ThunderBox's pusher checks (`sub`, `pipeline`,
    `job-name`, `aud`, its life) and the issuer its secret names, which the tenant allows, not `[web] root` (#1637).
    Its signature is checked against the keys at `<iss>/oidc/jwks`, as the pusher fetches them ([publish.md](publish.md),
    "The pusher");
13. three pull requests are refused with Zuul's own words: one that lists that job in `check` ("does not allow
    post-review job"), one with a job inheriting it ("is final and can not act as a parent"), and one with a job naming
    its secret ("Secrets must be defined in the same project in which they are used"). The workflow's token opens the
    pull requests of checks 1 to 13, and GitHub lets nobody approve their own, so the copy of gate for them leaves out
    its requirement of an approving review ([`sync.sh`](testbed/sync.sh));
14. the two-agent loop, in a run of its own ([below](#the-two-agent-loop-on-the-test-bed)): gate lets the builder's
    pull request in only once the reviewer approved its head, and Zuul merges it.

Zuul has no route from GitHub there, so the test bed sends Zuul the events GitHub would (`pull_request`,
`pull_request_review`, `issue_comment`, `status`), signed with the connection's webhook token. The build nodes are two containers with
Bazelisk, stand-ins for #1345's; their provider, [`testbed/providers.yaml`](testbed/providers.yaml), goes only into the
test bed's copy of aid-zuul-config. It replaces the production `zuul.d/providers.yaml` there: Compose cannot reach the
k3s pod. The production provider needs a separate live test on k3s. Each script a job names is a stand-in there that
only shows it ran, and the gate's full job runs a stand-in test ([`testbed/workspace/`](testbed/workspace)) that the
scenario's pull requests tell to sleep or to fail. The base job and its playbooks are copied unchanged; the pipeline
adaptations for the workflow's token are below.

**Secrets.** aid-zuul-config's secrets are encrypted for ThunderBox's project key, which no test bed has, and Zuul
decrypts every secret a job names as it loads the configuration. So `sync.sh` leaves `zuul.d/secrets.yaml`, and each
file of `zuul.d/` that defines or names a secret, out of the config copy. In their place it puts the test bed's own:
[`testbed/secret-probe.yaml`](testbed/secret-probe.yaml), with a fixture secret encrypted by
`deploy/zuul/encrypt-secret.sh` for a project key it makes. It also lists those jobs in `post` in the test bed's copy
(`zuul.d/testbed-post.yaml`). [`testbed/import-key.py`](testbed/import-key.py) gives Zuul that key before the scheduler
starts, as a restore of the keys would (`zuul-admin import-keys`). The key and the fixture are public in the copies,
and guard nothing. A checkout of aid-zuul-config without the guard runs without checks 12 and 13. aid-zuul-config's own
jobs with secrets are checked by Zuul on ThunderBox, on the pull request that changes them.

One thing differs from ThunderBox's Zuul: the token. For every pull request, Zuul's GitHub driver asks for the
repository's branch protection rules, which need the repository's **administration** permission. A workflow's token
cannot have it, and GitHub then answers with no repository at all, which Zuul fails on. So the test bed changes its
copy of that query to leave the rules out ([`testbed/patch.py`](testbed/patch.py)), and Zuul sees an unprotected
branch. ThunderBox's GitHub App (#1344) needs `Administration: read` for the same reason.

It runs in two public repositories of your own, for example `<you>/aid-zuul-testbed` and
`<you>/aid-zuul-config-testbed`, holding copies made by [`testbed/sync.sh`](testbed/sync.sh): of this folder and the
few files Zuul needs, and of an aid-zuul-config checkout. It has no secret: the run makes its keys and passwords, and
Zuul uses the workflow's own `GITHUB_TOKEN`, which reads the public config copy too. The config copy makes
aid-zuul-config's files public, which is safe only while they hold no secret in the clear: a secret there is encrypted
for ThunderBox's project key (#1351), which no test bed has. Once, make both repositories, and
let the test bed's workflows open pull requests:

```sh
gh repo create <you>/aid-zuul-testbed --public
gh repo create <you>/aid-zuul-config-testbed --public
gh api -X PUT repos/<you>/aid-zuul-testbed/actions/permissions/workflow \
  -f default_workflow_permissions=write -F can_approve_pull_request_reviews=true
git clone https://github.com/<you>/aid-zuul-testbed /tmp/aid-zuul-testbed
git clone https://github.com/<you>/aid-zuul-config-testbed /tmp/aid-zuul-config-testbed
```

Each run, from a checkout of this repository's branch and one of aid-zuul-config's (`main`, or the branch to test):

```sh
ci/zuul/testbed/sync.sh /tmp/aid-zuul-testbed /path/to/aid-zuul-config /tmp/aid-zuul-config-testbed
for copy in /tmp/aid-zuul-config-testbed /tmp/aid-zuul-testbed; do   # the config copy first
  git -C "$copy" add -A && git -C "$copy" commit -m "Test bed: $(git rev-parse --short HEAD)"
  git -C "$copy" push --force origin HEAD:main
done
gh workflow run testbed.yml --repo <you>/aid-zuul-testbed
gh run watch --repo <you>/aid-zuul-testbed --exit-status "$(gh run list --repo <you>/aid-zuul-testbed --limit 1 --json databaseId --jq '.[0].databaseId')"
```

The run takes about 20 minutes, most of it the gate's checks. It closes the pull requests Zuul did not merge and deletes their branches; `sync.sh` puts `main` back.
A failed run prints every service's log and the retained executor job and Ansible process outputs, including
failures before `job-output.txt` exists. The workflow keeps that output in its `testbed-diagnostics` artifact
before Compose cleanup. `run.sh` also writes it to `testbed-diagnostics.txt` (override with `TESTBED_DIAGNOSTICS`).
The disposable log store is writable by the executor sandbox's user, independently of the workflow runner's UID.
`sync.sh` records whether the copied trusted pipelines have `aid-events` reporters in `TESTBED_REPORTS`;
only those copies run the final MQTT lifecycle assertions. A broker connection by itself does not enable them.

### The two-agent loop on the test bed

Check 14 needs two accounts: the builder, who opens the pull request, and the reviewer, who approves it. So you are
the builder, with your own `gh`, and the run's token the reviewer. It runs alone, after a sync that keeps gate's
approval (`TESTBED_LOOP=1`), and `main` of the test bed's copy must dismiss an approval when a commit is pushed, as
AIDepartment's `main` does from #1355 ([`deploy/zuul/github.md`](../../deploy/zuul/github.md), "Branch protection").
Once, protect it so, with no approval required, which checks 1 to 13 need:

```sh
gh api -X PUT repos/<you>/aid-zuul-testbed/branches/main/protection --input - <<'JSON'
{"required_status_checks": null, "enforce_admins": false, "restrictions": null,
 "required_pull_request_reviews": {"dismiss_stale_reviews": true, "required_approving_review_count": 0}}
JSON
```

Each run: sync with `TESTBED_LOOP=1 ci/zuul/testbed/sync.sh …` and push both copies as above. Then open an issue and
a draft pull request that closes it, as a builder does, and dispatch the run with its number:

```sh
issue=$(gh issue create --repo <you>/aid-zuul-testbed --title "Loop: a change" --body "Built, reviewed and gated through the two-agent loop." | sed 's#.*/##')
git -C /tmp/aid-zuul-testbed fetch -q origin main && git -C /tmp/aid-zuul-testbed switch -q -C loop/$issue origin/main
mkdir -p /tmp/aid-zuul-testbed/testbed/gate && echo "# the builder's change" > /tmp/aid-zuul-testbed/testbed/gate/loop-$issue.txt
git -C /tmp/aid-zuul-testbed add testbed/gate && git -C /tmp/aid-zuul-testbed commit -qm "Loop: the builder's change" && git -C /tmp/aid-zuul-testbed push -q origin HEAD
pull=$(gh pr create --repo <you>/aid-zuul-testbed --draft --head loop/$issue --title "Loop: the builder's change" --body "Closes #$issue" | sed 's#.*/##')
gh workflow run testbed.yml --repo <you>/aid-zuul-testbed -f loop_pull=$pull
```

The scenario plays the GitHub side of both bots on it: check on the draft; ready, a comment review and `regate`, none
of which lets it in; the reviewer's approval of a held head, then the builder's push after it, which GitHub answers by
dismissing that approval; the reviewer's approval of the new head, which lets it in. Zuul merges it by rebase, and the
issue closes. Sync without `TESTBED_LOOP` before the next run of checks 1 to 13.

## On ThunderBox

ThunderBox's tenant ([`deploy/zuul/server/config/main.yaml`](../../deploy/zuul/server/config/main.yaml)) takes this
layout since #1344, which connects GitHub through Zuul's GitHub App on both repositories
([`deploy/zuul/github.md`](../../deploy/zuul/github.md)). It is the test bed's tenant ([`testbed/main.yaml`](testbed/main.yaml))
without the test bed's build node:

```yaml
- tenant:
    name: aid
    use-nodepool: false
    source:
      github:
        config-projects:
          - kristofk/aid-zuul-config:
              # A config project loads from master unless told (#1342's trial).
              load-branch: main
        untrusted-projects:
          - kristofk/AIDepartment:
              extra-config-paths: [ci/zuul/zuul.d/]
              # Only main's configuration, not every pushed branch's (#1344's trust model).
              exclude-unprotected-branches: true
      opendev.org:
        untrusted-projects:
          - zuul/zuul-jobs:
              include: [job]
```

## Checks

- **[`independence.mjs`](independence.mjs)** rejects AID ingress references in the CI runtime directories and the
  runbook's embedded webhook forwarder, checks that the private hooks network starts first, and checks the App URL.
  CI's Node suite exercises introduced dependencies as well as the repository's actual routes
  (`node --test ci/zuul-independence.test.mjs`). Optional UIs and event consumers are outside the CI path (#1506).
  The test bed also runs [`testbed/check-mqtt.py`](testbed/check-mqtt.py) in the pinned scheduler image: disconnected,
  failing and unacknowledged MQTT publications cannot wait for an AID subscriber or fail a build.

- **[`check.mjs`](check.mjs)**, with Node, no packages: the layout's rules. CI's Node tests run it
  ([`../zuul.test.mjs`](../zuul.test.mjs)).
  - no Zuul configuration at the repository's root, and the test bed's tenant reads AID's project from
    `ci/zuul/zuul.d/` and the config copy only for its `testbed/` (its build node and its jobs with a secret);
  - no pipeline and no base job in AID's project;
  - every playbook a job names exists under `ci/zuul/playbooks/`, and every role a playbook names exists beside it;
  - every job of AID's is `aid-bazel` or `aid-script`, or inherits one of them; every role runs only Ansible's
    `command` and `assert`, but `gate-plan`, which only reads the plan and hands its jobs to Zuul (`slurp`,
    `zuul_return`);
  - every script a job names is under `ci/` or `scripts/`, exists and is executable;
  - AID's one project stanza merges by rebase, runs the same jobs in `check` and `check-external`, and names in each
    pipeline only AID's jobs that run (or Zuul's `noop`);
  - `gate` runs `aid-gate-plan`, then every job its plan can name (`node ci/zuul/gate.mjs jobs`) and no other, each
    depending on `aid-gate-plan`, on its step's node, and only the quarantine runs not voting;
  - the test bed's node has the Bazelisk that `mise.toml` pins.
- **The test bed** ([above](#the-test-bed)), on Zuul itself, for every change to aid-zuul-config or the test bed, and
  to see a change to the library work before Zuul runs on ThunderBox.

Container targets, structural tests, runtime checks and the BuildKit retirement procedure are in [Container images](../../build/images/README.md).

Native providers own every production build label; tenant `aid` disables Nodepool fallback.
The Compose tenant does too: its `unknown_label_is_refused` scenario requires a configuration failure
naming an unknown label and no dispatched builds. Existing legacy requests need cancellation and
current-head replay; changing the flag alone cannot migrate them. The
[native-launcher migration runbook](../../deploy/thunderbox/zuul.md#completing-the-native-launcher-migration-1492)
requires a real successful Zuul dispatch alongside node readiness and direct SSH/remote Bazel checks.

For a focused native-launcher regression, dispatch the copied workflow with
`gh workflow run testbed.yml --repo <you>/aid-zuul-testbed -f native_launcher=true`.
It runs numbered checks 1–3: layout load, a successful real job dispatch, and unknown-label refusal.
The unknown-label PR converts deprecated `cleanup-run` entries to equivalent cleanup post-runs **in that
probe's proposed library only** so unrelated warnings cannot hide the real diagnostic (see #1889).
Production jobs now use cleanup post-runs (#1995); the conversion remains compatible
with older test-bed snapshots. The assertion requires Zuul's exact `The label "…" was not found.`
wording, a failed check, and zero dispatched builds; merely quoting `label: …` is insufficient.
The focused run omits gate/secrets scenarios and their whole-run MQTT assertions; it does not replace the
standard test bed for changes to those paths.
