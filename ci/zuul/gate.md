# Zuul's check and gate

The trusted pipelines are in `kristofk/aid-zuul-config`; this repository supplies jobs and
pipeline job lists under `ci/zuul/zuul.d/`. Cutover ownership and pending live evidence are
in [cutover.md](cutover.md). No draft preparation changes the live gate.

`check` runs the cheap jobs on each trusted head, drafts included. `gate` admits a ready PR
only with a successful current-head `aid/check`, a write-access approval and no `hold` or
blocking review. It tests the full required validation on a speculative rebase onto main and
those ahead of it. `aid/gate` must succeed before the App merges by rebase. The window is two;
a failed predecessor invalidates the speculative builds behind it. A test failure is not retried.

The cheap and full coverage jobs are implemented by #1520 and #1521. The gate job selection,
node requirements and remaining staging-node dependency are described below.

## The two-agent loop

How the build-and-review loop (`docs/review-loop.md`) runs on Zuul.

- **Enqueue: the reviewer's approval.** The reviewer approves a round with nothing blocking, a review "Approve" of the
  head it reviewed, where it used to post a plain comment review. The builder then marks the pull request ready. gate
  takes it once it is ready, approved by an account with write access, `check` passed on its head and without `hold`.
  Ready and the approval are both triggers, so whichever comes second enqueues it. Only the reviewer, or Kristóf, can
  approve: GitHub lets nobody approve their own pull request, and the accounts with write access are `kristofk`,
  `Opifex-Magnus` and `Artifex-Prime`. Without the approval, ready, a comment review or a `regate` comment lets in
  nothing (the test bed's check 13).
- **An approval covers the head it approved.** Zuul reads each reviewer's newest review, not the commit it was of
  (its GitHub driver's `getPullReviews`). What keeps a push after the approval from riding in on it is GitHub: from
  #1355, `main`'s protection dismisses an approval when a commit is pushed
  ([`deploy/zuul/github.md`](../../deploy/zuul/github.md), "Branch protection"), and the test bed's `main` does the
  same. A push also takes a pull request out of gate. So after the clean round the builder pushes only what is worth
  another round, and requests the review again after it: the reviewer's approval of the new head lets it in.
- **A request for changes keeps it out.** gate rejects a pull request whose newest review by an account with write
  access requests changes: Kristóf's request after a bot's approval keeps it out until he approves. A review submitted
  while the pull request is in gate does not take it out; `hold` does.
- **Hold and dequeue.** The label `hold`, or turning the pull request back into a draft, takes it out of gate and
  keeps it out. Removing the label, or marking it ready again, brings it back, if it still meets gate's requirements.
  Zuul has no dequeue command for the bots: `hold` is theirs.
- **Requeue.** A comment `regate` by an account with write access asks gate to take the pull request again: after a
  failure that was not its code (a flake, whose issue is filed first; a lost node), or when it should be in gate and
  is not (below). The requirements are read again, so `regate` lets in nothing that is not ready, approved and
  checked. After a failure caused by its code, the builder pushes the fix and requests the review again.
- **What Zuul reports, and checking that it was queued.** gate reports on the pull request as the check run
  `aid/gate`: queued, then in progress, then success and the merge, or failure with a comment that names the failed
  job and links its log. About 10 minutes after the pull request is ready, approved and `aid/check` passed on its
  head, `aid/gate` is there. If it is not (GitHub's event came late or not at all), comment `regate`, once. If that
  starts nothing either, tell the orchestrator.
- **What wakes the bots.** The loop's automations ([`docs/review-loop.md`](../../docs/review-loop.md), "How AID routes
  the events") react to GitHub's review requests, submitted reviews and closed pull requests, never to a queue's
  events, so no queue-specific event is needed. The approval reaches the builder as every review does
  (`--review-in`). Zuul's merge closes the pull request on GitHub, and `--pr-closed` forgets its threads as it does
  after any merge. The builder hears of a failure in gate as it hears of any check's: T3 Code's `watch_pull_request`
  wakes it when `aid/gate` fails or Zuul comments. Zuul's own events reaching AID, with no forge in between, are
  #1349; an automation on them can then take the watch's place.
- **The skills.** `review-pr` approves the clean round. `work-on-issue` marks the pull request ready after it, pushes
  after the approval only with a new round, and takes the queue's commands from the repository's docs on merging. The
  skills take draft/check behavior and queue commands from the repository docs.
- **State commits.** The orchestrator's `internal/`-only commits to `main` don't wait for an empty gate. Zuul does not
  restart gate on such a push ([What changes](#what-changes)): its builds go on against the `main` they started from,
  and GitHub's rebase puts their commits on top of the state commit. A state commit changes only notes in
  `internal/`, which no gate job builds or tests, so what lands is the tree gate tested plus files no test reads. A
  pull request that changes the same file under `internal/` does not rebase: Zuul reports the merge failure, and the
  builder rebases and pushes. Waiting for an empty gate would hold the orchestrator's tick for as long as gate's runs
  take, without making any test truer.

## Merging

`merge-mode: rebase` keeps main linear. Zuul tests the speculative source tree, then GitHub
rebases the PR's commits: the resulting commit IDs differ. Only Zuul's App updates main except
for the administrator's recorded state/recovery exception. A PR containing a merge commit
must be rebased before GitHub can merge it this way.

Each job has its own timeout, two hours from the trusted base unless a job reduces it.
Waiting for a node is not part of that timeout. A timeout fails the buildset. Returning a PR
to draft or adding `hold` dequeues it, though its check may remain pending until a later run.

## The full job on a build node

`aid-bazel-everything` runs `bazel test --config=ci --config=zuul //...` on the farm. The Zuul
node has no compiler and no local fallback. The farm toolchain, dependency metadata and
`.bazelrc` own execution; build logic stays in Bazel and repository scripts. The production
proof is build `a52d31e7a74846b386930c3495c54115` on 2026-11-15: 1822 actions, 30 tests passed,
21 executed, in 1519.002 seconds (#1485). It proves that Bazel job, not coverage of other gate jobs.

## The gate's jobs

Gate runs what CI's queue run on GitHub Actions runs before `Every package passed` (#1521): each of its steps is a job
of AID's ([`zuul.d/jobs.yaml`](zuul.d/jobs.yaml)), and each job runs the step's commands through
[`gate.sh`](gate.sh), never a copy of them in a playbook. Bazel's job stays the Bazel command it was.

**The plan decides which run.** `aid-gate-plan` comes first. It checks the quarantine list (`node ci/quarantine.mjs
check`, as the plan job does in every layer), then plans the change with the plan CI's queue run reads,
`ci/affected.mjs` ([`gate.mjs`](gate.mjs)), against `origin/main`: in Zuul's prepared checkout that is the state ahead
of this change, `main` with the pull requests ahead of it in gate, so the plan is the change's own. The plan names the
jobs the change needs, and its role hands them to Zuul (`zuul_return`'s `child_jobs`,
[`playbooks/roles/gate-plan/`](playbooks/roles/gate-plan/tasks/main.yaml)). Every other job of gate depends on
`aid-gate-plan`, so Zuul runs those it names and skips the others, which ask for no node. A plan that cannot list the
changed files plans everything, as the queue's does. The plan is written with the build's logs (`gate-plan.json`).

**What fails the gate.** A planned job that fails, times out, or gets no node (`NODE_FAILURE`) fails the buildset, and
Zuul merges nothing. A step left out only because the plan left it out is `SKIPPED`, as a job the plan left out is
skipped on GitHub Actions. If the plan itself fails, every job behind it is skipped and the gate fails. A planned step
whose node lacks what it needs fails: an image job checks its node's BuildKit and endpoints, staging its Docker, the
ingress suite that `aid` finds the Swift runtime, before they start. Nothing passes for want of Docker.

| CI's queue run (`ci.yml`) | Gate's job | Planned when (`ci/affected.mjs`) | Node |
| --- | --- | --- | --- |
| the plan job's quarantine check and plan | `aid-gate-plan` | always | the PR node |
| `bazel`: `bazel test //...` | `aid-bazel-everything` | `bazel`: Swift inputs | the PR node, on the farm ([The full job](#the-full-job-on-a-build-node)) |
| `bazel`: the architecture's actual-edge check | `aid-bazel-graph`: `node ci/bazel-graph.mjs` | `bazel` | the PR node |
| `ingress`: the ingress suite | `aid-gate-ingress`: `aid` built on the farm, then [`ci/ingress-suite.sh`](../ingress-suite.sh) | `ingress`, whole or the applications' part | the PR node |
| `images`, one per application (`app-image.yml`) | `aid-gate-image-<app>`, one per `apps/<app>/ci/check-image.sh` | `images` names it | the gate's image node |
| `release-folder` and `aid-images` | `aid-gate-aid-images`: `deploy/release-folder.sh`, then AID's three images | `aid_images` | the gate's image node |
| `farm-worker` | `aid-gate-farm-worker`: `node ci/build-farm-worker.mjs build` | `farm_worker` | the gate's image node |
| `zuul-node-image` | `aid-gate-zuul-node-image`: `node ci/zuul-node-image.mjs build` | Zuul's infrastructure check | the gate's image node |
| `infra-checks` | `aid-gate-infra-build-farm`, `aid-gate-infra-zuul`: `ci/infra-check.sh <check>` | `infra_checks` names it | the PR node |
| `staging` (and its release folder) | `aid-gate-staging`: `deploy/release-folder.sh`, then `deploy/test/staging/run.sh` | `staging`: `VERSION` changed | the staging node |
| `quarantine-swift`, `quarantine-node` (not blocking) | `aid-gate-quarantine-swift`, `aid-gate-quarantine-node`, `voting: false` | a test is in quarantine | the PR node |

[`check.mjs`](check.mjs) holds gate's list in [`zuul.d/project.yaml`](zuul.d/project.yaml) to the plan's jobs
(`node ci/zuul/gate.mjs jobs`), each behind `aid-gate-plan`, on its node, and only the quarantine runs not voting.

**What is not carried over.** The queue's images kept for main (#466): post builds and checks each image again on the
merge ([`publish.md`](publish.md)), with the same commands ([`steps.sh`](steps.sh), which post's scripts and gate's
share). The release folder is built in each job that needs it, clean, rather than once for two jobs through the run's
Actions cache: no CI artifact or cache carries it between jobs (#1286). The quarantine runs' records are kept with the
build's logs (`quarantine-swift.json`, `quarantine-node.json`); `node ci/quarantine.mjs history` still reads only
GitHub Actions' artifacts. The cheap layer's steps (the Node suites, the CI and workflow policy tests) are check's
(#1520).

**The nodes.** The PR node ([`deploy/zuul/node/`](../../deploy/zuul/node/README.md)) runs Bazel's, the ingress
suite's, the infrastructure checks' and the quarantine runs' jobs, one at a time, with the farm's `dev` credential; its
image carries the Swift runtime libraries, without a compiler, for the farm-built `aid` the ingress suite starts. The
gate's image node ([`deploy/zuul/gate-node/`](../../deploy/zuul/gate-node/README.md)) runs the image jobs: the post
node's image, with a BuildKit of its own ([`deploy/zuul/gate-buildkit/`](../../deploy/zuul/gate-buildkit/README.md)),
the scratch registry and the image-check namespace, no secret and no farm token. The staging node
([`deploy/zuul/staging-node/`](../../deploy/zuul/staging-node/README.md)) is Docker, for staging alone.

### Turning the gate's jobs on

The gate image node and provider `aid-k3s-gate` are live (orchestrator, 2026-11-16 09:20Z).
The staging node does not exist yet: a `VERSION` change cannot pass gate until Kristóf decides
its placement and the node is provided. Keep that job required for releases. The original
rollout procedure below records the dependencies; current cutover ownership and evidence are
in [cutover.md](cutover.md).

0. **Kristóf's decisions**: the gate's image node's 128 MiB and its BuildKit's 2560 MiB of ThunderBox's node memory,
   beyond the node's cap as it stands; and where the staging node runs
   ([`deploy/zuul/staging-node/`](../../deploy/zuul/staging-node/README.md), "Undecided").
1. **The PR node's image** (the admin): the node image from a `main` with this change, which carries the Swift
   runtime, applied to the PR node ([`deploy/zuul/node/README.md`](../../deploy/zuul/node/README.md), "Applying it").
2. **The gate's image node and its BuildKit** (the admin, after a heads-up to the orchestrator; ThunderBox's session
   for its grants): their READMEs' "Applying it".
3. **The staging node**, as step 0 decides.
4. **aid-zuul-config** (the orchestrator merges, after the other bot's review): the labels `aid-gate-node` and
   `aid-staging-node`, and the providers with each node's host key, as `aid-post-node`'s are. Check:
   `/api/tenant/aid/config-errors` is `[]`.
5. **A gate run** of an ordinary pull request at the switch: `aid-gate-plan`'s log names the planned jobs, they run,
   and the others are `SKIPPED`.

The testbed's `testbed/check-github-gate.py` and `testbed/scenario.py` exercise approval,
current-head handling, holding, timeouts, speculative ordering and regating independently
of production job coverage. Historical rollout evidence is in [comparison.md](comparison.md).
