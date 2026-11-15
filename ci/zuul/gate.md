# Zuul's check and gate: what Mergify does today

How Zuul's pipelines do what CI's two layers and Mergify's merge queue do for `main` today (#1347, umbrella #1341).
Today's rules are in [`.mergify.yml`](../../.mergify.yml), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
and [`docs/development.md`](../../docs/development.md), "Merging: the merge queue". Zuul's are in two places
([README.md](README.md), "Trusted and untrusted"):

- **the pipelines** `check`, `gate` and `hold`, in kristofk/aid-zuul-config's `zuul.d/pipelines.yaml` (trusted);
- **which jobs each runs, and the merge mode**, in [`zuul.d/project.yaml`](zuul.d/project.yaml), and the jobs in
  [`zuul.d/jobs.yaml`](zuul.d/jobs.yaml) (AID's own, untrusted).

Until the switch (#1355) Mergify still merges, and Zuul's gate takes no pull request of AIDepartment's: `main`'s
branch protection requires `Every package passed`, which only Mergify's queue draft carries, and Zuul enqueues a pull
request into a dependent pipeline only when GitHub would let it merge (its GitHub driver's `canMerge`: "required status
checks are missing"). Every row below was seen on the test bed ([Seen working](#seen-working)).

The ordinary production runs and their limits are in [comparison.md](comparison.md) (#1353): no reporting-only gate
run or production gate timing was observed, and the missing jobs/rollout problems are tracked separately. The test
bed's pipeline proof does not establish production job parity.

## From Mergify to Zuul

| Today (Mergify / Actions) | On Zuul | Where |
|---|---|---|
| `Cheap checks passed` required to enter the queue | `check`'s cheap jobs, and gate's requirement `status: aid-zuul:aid/check:success`: `check` passed on the pull request's head. gate is triggered when check's check run completes successfully, so a ready pull request enters by itself once check passes on a new head | `project.yaml` `check`; gate's `require` and `trigger` |
| `Every package passed` required to merge | gate's full jobs, on the pull request merged onto `main` and onto those ahead of it; Zuul merges only when every one passed | `project.yaml` `gate` |
| serial queue, batches of 2 | gate's speculative queue: a window of two, so two pull requests are tested at once, the second on top of the first ([Speculative depth](#speculative-depth)) | gate's `window` |
| fast-forward merges | `merge-mode: rebase`: Zuul tests the pull request's commits rebased onto the state ahead of them, and merges with GitHub's rebase. `main` stays linear ([Merging](#merging)) | `project.yaml` |
| the `hold` label blocks queueing | gate's `reject: label: hold`; labelling `hold`, or turning the pull request back into a draft, takes it out of gate (the pipeline `hold`); removing the label enqueues it again | gate's `reject` and `trigger`, the pipeline `hold` |
| ready means queued; a draft is never queued | ready **and approved**: gate's `require: draft: false` and an approving review by an account with write access, and its triggers `ready_for_review` and that review. Ready alone no longer queues: the reviewer's approval is what lets a pull request in ([The two-agent loop](#the-two-agent-loop), #1348) | gate's `require` and `trigger` |
| 4 h checks timeout (2 h a job) | each job's `timeout`: the base job's 2 hours, less where a job sets it ([Timeouts](#timeouts)) | aid-zuul-config's `base`, `jobs.yaml` |
| dequeue and `@mergifyio requeue` | a comment `regate` by an account with write access enqueues a pull request again; a push dequeues it (`dequeue-on-new-patchset`), and it comes back once check passes on the new head | gate's `trigger` |
| queued, dequeued labels; the "Merge Queue Status" comment | gate's check run `aid/gate`: queued, in progress, success, failure (with a comment naming the failed job and its log), cancelled | gate's reporters |

### What changes

Each of these is a difference from today, kept on purpose or open for a later issue:

- **Ready is not enough: an approval is.** Mergify queues a pull request that is ready. gate also requires an
  approving review by an account with write access, and no request for changes by one (#1348). The builder marks its
  pull request ready, which it could do at any time; only the reviewer, or Kristóf, can approve it
  ([The two-agent loop](#the-two-agent-loop)).
- **A draft runs `check`.** Actions runs nothing on a draft; Zuul's `check` runs on every push by an account with
  write access (#1344's trust model, which keeps `ready_for_review` out of `check`'s triggers: a maintainer marking a
  stranger's pull request ready would run it with the farm's credential). check holds only the cheap jobs.
- **The commit on `main` is not the tested commit.** Mergify fast-forwards `main` onto the commit its run tested. Zuul
  tests its own rebase of the pull request's commits, then asks GitHub to rebase and merge them: new commits, with the
  tree Zuul tested whenever `main` moved only through the gate. GitHub's rebase merge cannot fast-forward, and no merge
  mode of Zuul's GitHub driver does.
- **A push to `main` outside the gate does not restart it.** Mergify restarts the check in front
  (`reset_on_external_merge`). Zuul's builds go on against the `main` they started from, and GitHub's rebase then puts
  their commits on the newer one. From #1355 only Zuul moves `main` but for the orchestrator's `internal/`-only state
  commits, and those don't wait for an empty gate ([The two-agent loop](#the-two-agent-loop), "State commits").
- **`hold` leaves `aid/gate` as it was.** Zuul removes a superseded item without a dequeue report (the scheduler's
  `_doSupercedeEvent`, `removeItem`): its buildset is recorded `DEQUEUED`, but a pull request taken out by `hold`
  keeps its `aid/gate` check run queued or in progress (pending on the test bed) until it enters gate again, or a
  push starts a new one. It never shows success, so branch protection still refuses it (#1355).
- **No labels.** `queued` and `dequeued` were Mergify's labels; `aid/gate`'s state shows the same in lists.
- **The gate's own check.** Zuul runs `check`'s cheap jobs once per head and gate's full ones once per speculative
  state. A failure that a rebase onto newer pull requests caused shows in gate, as it shows in Mergify's queue draft.

## The two-agent loop

How the build-and-review loop ([`docs/review-loop.md`](../../docs/review-loop.md)) runs on Zuul (#1348). Until the
switch (#1355) Mergify still merges, and the commands are its own ([`docs/development.md`](../../docs/development.md),
"Merging: the merge queue"); from #1355 they are these.

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
  events, so none named Mergify's and none changes. The approval reaches the builder as every review does
  (`--review-in`). Zuul's merge closes the pull request on GitHub, and `--pr-closed` forgets its threads as it does
  after Mergify's. The builder hears of a failure in gate as it hears of any check's: T3 Code's `watch_pull_request`
  wakes it when `aid/gate` fails or Zuul comments. Zuul's own events reaching AID, with no forge in between, are
  #1349; an automation on them can then take the watch's place.
- **The skills.** `review-pr` approves the clean round. `work-on-issue` marks the pull request ready after it, pushes
  after the approval only with a new round, and takes the queue's commands from the repository's docs on merging. The
  skills name neither Mergify nor Zuul, so they work on both, and the switch (#1355) changes only those docs.
- **State commits.** The orchestrator's `internal/`-only commits to `main` don't wait for an empty gate. Zuul does not
  restart gate on such a push ([What changes](#what-changes)): its builds go on against the `main` they started from,
  and GitHub's rebase puts their commits on top of the state commit. A state commit changes only notes in
  `internal/`, which no gate job builds or tests, so what lands is the tree gate tested plus files no test reads. A
  pull request that changes the same file under `internal/` does not rebase: Zuul reports the merge failure, and the
  builder rebases and pushes. Waiting for an empty gate would hold the orchestrator's tick for as long as gate's runs
  take, without making any test truer. Until #1355 Mergify's `reset_on_external_merge` stays as it is.

## Merging

`merge-mode: rebase` is a property of AID's project ([`zuul.d/project.yaml`](zuul.d/project.yaml)). Zuul's merger
builds each pull request's state by rebasing its commits onto `main` and onto the pull requests ahead of it, and its
GitHub reporter merges with GitHub's `rebase` method. The repository allows rebase merges (`allow_rebase_merge`), which
Zuul reads with the App's Contents permission ([`deploy/zuul/github.md`](../../deploy/zuul/github.md)). A pull request
with a merge commit in it cannot be rebased by GitHub; push it rebased instead.

## Timeouts

Mergify's `checks_timeout` bounds the whole queue run: 4 hours since 2026-11-13, for a run that builds every app image
one at a time, with each job of it limited to 2 hours. Zuul has no timeout for a buildset, only for each job: the base
job's `timeout: 7200` (aid-zuul-config's `zuul.d/jobs.yaml`), and less where a job of AID's sets it (the lint jobs, 10
minutes). Waiting for a build node counts against no timeout. A job that runs out is `TIMED_OUT`, which fails its
buildset. The app images become jobs of their own (#1350, #1353), each with its own timeout, so no limit has to cover
them all one after another.

## The full job on a build node

`aid-bazel-everything` runs `bazel test --config=ci --config=zuul //...`. `.bazelrc`'s `zuul` is the farm as a Zuul
build node reaches it (#1345's node, `deploy/zuul/node/`):

- **the cluster's address**, `frontend.build-farm.svc:8980`, as CI's runner pods reach it. The node's own system
  bazelrc names its credential helper for that host. `--config=remote` alone would not do: the checkout's `.bazelrc`
  is read after the node's system one, and its `remote` names the boxes' address, `build-farm:8980`;
- **the hermetic toolchain** (`--config=hermetic`): a node has no Swift and no C compiler, and without a `swiftc` on
  PATH rules_swift makes no toolchain at all (`swift_autoconfiguration`). swift.org's toolchain, downloaded on the
  node, runs on the farm's workers;
- **no local fallback**: an action the farm can't run fails, rather than run on a node that can't build it.

Under `dev`, remote's instance: #1345's node is the pull requests', and the gate's `main` (#1298) comes with its own
nodes and credential (#1351).

**Not working yet (#1485).** The node is not deployed, so the job has not run there. From a box, with a fresh output
base and no `swift`, `clang`, `gcc` or `cc` on the repository rules' PATH, `--config=ci --config=zuul` (the boxes'
farm address in place of the cluster's) got through analysis and ran 47 actions on the farm. After 38 minutes Bazel's
circuit breaker stopped it ("Call not executed due to a high failure rate"). The farm does build Swift with its
workers' own toolchain, but that toolchain is found through a local `swiftc`. #1485 gives the job a toolchain that
works from the node.

## Speculative depth

Zuul's gate tests the pull requests at the head of its queue at once, each on top of those ahead of it: the window.
The window is two, its floor one and its ceiling two. A failure halves it, to one, and each pull request that merges
adds one again, back to two.

- **Two, as today's throughput.** A Mergify check takes a batch of two pull requests in one run. Zuul's window of two
  tests two at once, each as its own version, and merges each as it passes, so the first never waits for the second.
- **What bounds it on ThunderBox.** Each pull request in the window runs gate's full jobs: Bazel's build and test of
  everything on the farm. The farm is shared with CI's runners until #1355, and the build nodes come out of the node
  budget in [`deploy/thunderbox/zuul.md`](../../deploy/thunderbox/zuul.md) (#1345). Two keeps the gate's load on the
  farm near today's, one queue run of two pull requests. A window above the number of nodes gains nothing: the jobs
  behind wait for a node. Raise the ceiling with the nodes, once #1353 has measured the times.
- **What a deeper window costs.** When a pull request fails, every build behind it is thrown away and run again
  without it. With a window of two that is one pull request's builds, as a failed batch of two is today.

## Flakes

A test that fails by chance in gate fails the pull request it ran for, and throws away the builds of every pull request
behind it in the window. Those start again without the failed one, and merge if they pass. Nothing is retried:
Zuul runs a job again only when its node or its pre-run failed (`attempts`, `RETRY_LIMIT`), never for a failed test,
which keeps #225's rule that a test runs once per version.

The failed pull request leaves gate with `aid/gate` failed and a comment. A `regate` comment enqueues it again, as
`@mergifyio requeue` does today. That is for a failure that was not its code: the flake's issue is filed first, and a
test that keeps failing goes into quarantine ([`docs/development.md`](../../docs/development.md), "Flaky tests"). The
quarantine works the same on Zuul: Bazel's BUILD files and `build/quarantine.bzl` leave a quarantined test out of
`bazel test //...`, so gate does not run it, and the quarantine runs are no gate job (they come with #1350 and #1353).
A flake hurts more in a speculative queue than in Mergify's serial one, since it resets the pull requests behind it
too, which is one more reason the window stays at two until the flake handling milestone has the open flakes down.

## Seen working

The test bed ([README.md](README.md), "The test bed") runs each row on Zuul 14.3.0, with copies of this folder and of
kristofk/aid-zuul-config ([`testbed/scenario.py`](testbed/scenario.py), checks 6 to 10; check 2 for check's cheap jobs).
The gate's full job runs a stand-in test there ([`testbed/workspace/`](testbed/workspace)) that each pull request of
the scenario tells to sleep or to fail, so the test bed shows the pipelines, not AID's build on the farm
([The full job on a build node](#the-full-job-on-a-build-node)). On the test bed, check reports a commit status instead of a check run, so gate's
requirement and trigger read that status ([`testbed/sync.sh`](testbed/sync.sh)).

Run [43605712074](https://github.com/Artifex-Prime/aid-zuul-testbed/actions/runs/43605712074) on 2026-11-14, of
AIDepartment's `ci/zuul` at `ef66f0f0` and kristofk/aid-zuul-config at `4a48a818` (#1347's `d466b39f` merged with the
external guard of aid-zuul-config#3, `203c5acb`, which check 5 needs), passed all ten checks in 24 minutes:

| Row | Check | Seen |
|---|---|---|
| `Cheap checks passed` | 2, 7, 10 | check runs the cheap jobs; a draft whose check passed stays out of gate until it is ready; a ready pull request stays out until check passes on its head, and that alone brings it in |
| `Every package passed` | 7 | gate runs `aid-bazel-everything`, and not check's jobs |
| batches of 2 | 9 | two pull requests' builds run at once, the second's item behind the first's, in a window of two |
| fast-forward | 7 | Zuul merges by rebase: `main` moves forward with no merge commit |
| `hold` | 8 | a ready pull request with `hold` stays out; labelled `hold` in gate, it leaves (`DEQUEUED`, unmerged); without it, it comes back, passes and merges |
| checks timeout | 6 | a job that runs past its `timeout` is `TIMED_OUT`, and check fails |
| requeue | 9, 10 | a failed pull request enters gate again on `regate`, and merges; a push takes a pull request out of gate, and check passing on the new head brings it back, with no other event |
| labels, the status comment | 7, 9 | `aid/gate` success and failure on the pull request, and a comment naming the failed job |
| flakes | 9 | the first fails; the second's build on top of it is `ABORTED` and run again without it, passes and merges |

### The two-agent loop on the test bed

Checks 6 to 10 run with a copy of gate that leaves out its approval: the run's token opens their pull requests, and
nobody may approve their own ([`testbed/sync.sh`](testbed/sync.sh)). Check 13 runs alone, with the approval kept, on a
pull request the builder opened as itself, which the run's token reviews ([README.md](README.md), "The two-agent loop
on the test bed").

Run [43623232006](https://github.com/Artifex-Prime/aid-zuul-testbed/actions/runs/43623232006) on 2026-11-14, of
AIDepartment's `ci/zuul` at `8b8d0430` and kristofk/aid-zuul-config#7 at `0713a8d0`, took issue
[Artifex-Prime/aid-zuul-testbed#87](https://github.com/Artifex-Prime/aid-zuul-testbed/issues/87) through the loop.
Its pull request, #88, was opened by `Artifex-Prime`, the builder, and reviewed by `github-actions[bot]`. GitHub had
refused the builder's own approval ("Can not approve your own pull request"). With no Mergify:

| Step | Seen |
|---|---|
| a draft | check ran on it and passed |
| a comment review, then ready | stays out: nobody approved it |
| `regate` | stays out: a comment does not stand in for an approval |
| approved while held | stays out |
| the builder's push after the approval | GitHub dismissed the approval; check passed on the new head; without `hold` it stays out, as its one approval was of the old head |
| the reviewer's approval of the new head | gate ran `aid-bazel-everything`, it passed, and Zuul merged it by rebase: `main` linear, both commits on it |
| the issue | the merged pull request names #87 as the issue it closes, but GitHub closed no issue for the merge by the workflow's token, so the check closed it (completed). On ThunderBox Zuul merges as its App, not a workflow token; that its merge closes the issue is first seen at #1355 |
