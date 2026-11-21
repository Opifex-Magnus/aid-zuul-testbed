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
  nothing (the test bed's check 14).
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
- **Requeue.** An account with write access posts a comment whose entire body is `regate` to ask gate to take the
  pull request again (case-insensitive, surrounding whitespace allowed). `please regate`, or an explanation with
  `regate` on another line, does not match the anchored trigger. Use it after a
  failure that was not its code (a flake, whose issue is filed first; a lost node), or when it should be in gate and
  is not (below). The requirements are read again, so `regate` lets in nothing that is not ready, approved and
  checked: `aid-zuul:aid/check:success` must cover the current head. It cannot recover a failed `check`; use
  [same-head check recovery](../../deploy/zuul/web.md#recovering-check-on-the-same-head) for a recorded unrelated
  failure there. `recheck` has no trigger. After a failure caused by its code, the builder pushes the fix and
  requests the review again. Before retrying, check `gh pr view <n> --json mergeable,mergeStateStatus`: a conflict
  with main is silently refused before enqueue, including through the admin API. `UNKNOWN` needs another check
  after GitHub finishes calculating; `CONFLICTING` needs a rebase onto current `origin/main`, conflict resolution,
  affected tests, a push with `--force-with-lease` and a new review/check of that head. See
  [enqueue refusal diagnostics](#enqueue-refusal-diagnostics).
- **What Zuul reports, and checking that it was queued.** gate reports on the pull request as the check run
  `aid/gate`: queued, then in progress, then success and the merge, or failure with a comment that names the failed
  job and links its log. About 10 minutes after the pull request is ready, approved and `aid/check` passed on its
  head, `aid/gate` is there. If it is not, check mergeability first as above; once mergeable, post a whole-comment
  `regate`, once (GitHub's event may have come late or not at all). If that
  starts nothing either, tell the orchestrator. An approved pull request of kristofk/aid-zuul-config enters gate too,
  which has no jobs for it: `aid/gate` goes from queued to skipped, and the orchestrator merges it (#1660).
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

## Enqueue refusal diagnostics

In Zuul 14.3.0, the dependent manager checks GitHub's mergeability before it creates a queue item.
A refusal such as `Github detected a merge conflict` normally reaches only DEBUG logs; neither the
`enqueue` nor `merge-conflict` reporter handles this pre-admission refusal. The latter handles conflicts
found while preparing an admitted item. An admin enqueue returning HTTP 200 can still leave the PR out
of gate. Check mergeability and actual `aid/gate` admission rather than repeating retries (#1880).

There is a supported diagnostic option for GitHub event triggers:
[`pipeline.trigger.github.debug`](https://zuul-ci.org/docs/zuul/14.3.0/drivers/github.html#attr-pipeline.trigger.github.debug).
The trusted `kristofk/aid-zuul-config` owner can add `debug: true` to gate's existing whole-comment
`regate` trigger (preserving its write permission and all pipeline requirements). In that version,
`DependentPipelineManager.areChangesReadyToBeEnqueued` then adds the refusal reason to warnings and
`PipelineManager._reportNonEnqueuedItem` sends it via the **failure** reporters, which already post a
GitHub check and comment in the trusted gate configuration. Merely adding a `merge-conflict` reporter
or changing its message does not fix this path. These code paths are in the
[Zuul 14.3.0 source release](https://pypi.org/project/zuul/14.3.0/#files).

That trusted-config change needs a regression test: an otherwise eligible conflicting PR plus bare
`regate` must report the merge-conflict reason without running jobs; an eligible non-conflicting PR
must still enter gate, and permission/approval/hold checks must remain enforced. The refusal also
reaches gate's `aid-events` failure reporter and records a failure buildset; test that the loop's
automations handle it as an enqueue refusal rather than a gate flake or code failure. This repository's
job definitions cannot change trusted pipeline triggers, so this documents a follow-up, not an enabled
live diagnostic. Trigger debug is verbose and only applies to that trigger; it does not cover admin
enqueue. Reporting admin pre-admission refusals remains a separate upstream follow-up, without a
local Zuul patch.

## Merging

`merge-mode: rebase` keeps main linear. Zuul tests the speculative source tree, then GitHub
rebases the PR's commits: the resulting commit IDs differ. Only Zuul's App updates main except
for the administrator's recorded state/recovery exception. A PR containing a merge commit
must be rebased before GitHub can merge it this way.

Each job has its own timeout, two hours from the trusted base unless a job reduces it.
Waiting for a node is not part of that timeout. A timeout fails the buildset. Returning a PR
to draft or adding `hold` dequeues it, though its check may remain pending until a later run.

## The full job on a build node

#1867's routing change selects `aid-gate-compute` (`aid-gate-compute-pod-node`)
for `aid-bazel-everything`, with the unchanged `-Xmx3g`, remote execution and
minimal downloads. All other non-image gate jobs, including plan, inherit the
same compute pool from `aid-gate`; image jobs retain `aid-gate-image` and staging
retains `aid-staging`. Check jobs still inherit trusted base's check pool.

**Activation hold:** the orchestrator must install the merged prerequisites from
#1915 and its exact trusted provider companion, then prove the new label, farm
dev-only identity and deletion on success/abort. Production compute admission,
image admission, gate window and scratch semaphore remain one. The one compute
slot serializes non-image gate jobs without consuming check slots; it does not
add farm worker capacity. Measure gate wall-clock and check queue latency at
activation, accepting the longer serial critical path explicitly.

The full OCI graph's external repositories can occupy client storage despite
minimal remote output downloads. #1814 documented eviction with a 5 GiB home;
the first #1926 activation gate confirmed that limit failed three times. The
orchestrator's revised decision selects 11 GiB home / 12 GiB scratch and retires
both idle BuildKits, preserving all other tenant quotas. The current model and
headroom are recorded in the [compute README](../../deploy/zuul/gate-compute-node/README.md).
This is a capacity choice to validate, not a completed production activation proof.
The footprint prerequisite #2002 supplies the measured cache/compact-log settings
([investigation](gate-compute-storage.md)); it must merge before this routing
activation is explicitly released by the orchestrator.

**Keep draft and hold; do not release the routing hold** until review, the
orchestrator's live PodTemplate apply, and explicit gate release. Activation needs
full graph proof within that envelope
or a reviewed capacity/storage change. If it is evicted again, the next step is
raising the kubelet dataset, not shrinking other tenants. Do not regate without
explicit authorization.

The farm toolchain, dependency metadata and
`.bazelrc` own execution; build logic stays in Bazel and repository scripts. The production
proof is build `a52d31e7a74846b386930c3495c54115` on 2026-11-15: 1822 actions, 30 tests passed,
21 executed, in 1519.002 seconds (#1485). It proves that Bazel job, not coverage of other gate jobs.

The full OCI graph in #1701 exhausted the Bazel Java heap after action completion (#1842,
exit 33); no Kubernetes OOMKill, eviction or memory pressure was observed. The job explicitly selects the existing
6 GiB compute node, leaving 3 GiB outside the explicit 3 GiB heap for native JVM memory,
client/repository work, SSH and logs. The compute pool reserves 384 MiB; its 6 GiB hard limit and namespace
allocation stay unchanged by routing. The repository-wide 25% heap policy remains the default for other jobs.
The startup override and `nodeset: aid-gate-compute` travel together; the general PR node has
only 2 GiB and cannot support this heap. Targeted tests check configuration and headroom,
not whole-graph peak usage: that proof requires a future operator-authorized full gate.
Sharing this node does not make the Bazel job a scratch-registry client: its Bazel role
runs `test //...` against the farm/CAS, without image upload or runtime image pulls.
It takes no `aid-scratch-registry` semaphore. The `gate.sh` operations `image`,
`aid-images`, `farm-worker` and `zuul-node-image` push scratch images and pull them
for runtime checks; those lifetimes require the registry semaphore and cleanup post-run,
regardless of which nodeset runs them.
Do not retry the exhausted gate to validate this change.

Execution RPC deadlines include queue time. Zuul therefore uses
[`zuul-grpc-service-config.json`](../../build/farm/zuul-grpc-service-config.json), which retains the
10-second capability and 60-second cache deadlines but omits the Execution service's absolute
deadline, for both Execute and WaitExecution (#1770). This matches Bazel's default treatment of
Execution streams: workers enforce the action's execution timeout after it starts; waiting for a
slot cannot consume that budget. An unavailable endpoint still fails its bounded capability calls.
A scheduler that accepts work and never completes it is bounded by the job timeout (two hours for
the full job), rather than a 120-second RPC deadline. This intentionally trades early detection of
a stalled scheduler for allowing a saturated healthy queue to drain.

Zuul retains the failure-rate threshold of 100 so transport retries cannot cause an early
whole-invocation breaker abort (#1719). The default `remote` configuration retains its deadlines,
breaker and local fallback. `ci/bazel-fallback.mjs` covers the old early-abort trigger, retry
exhaustion despite that threshold, a saturated two-slot queue waiting over 120 seconds, stream
reconnection through WaitExecution, harness SIGINT interruption of a stalled scheduler (not an actual Zuul/Ansible timeout), and refused service.

The voting `aid-nightly` job runs the full simulator through `ci/zuul/publish/nightly.sh`,
only at the periodic timer admitted by the nightly clock (04:00 Madrid). The script runs both
this lab and the full Bazel suite and fails if either fails, so the existing `aid-nightly-report`
opens/comments on the `ci-nightly` issue for a simulator failure. A separate unreported periodic
job would not provide that notification. See [publishing](publish.md#the-nightly).

The independent isolated-node proof took 1502 seconds (about 25 minutes).
The box queue case alone took 981 seconds: it deliberately exceeds the real legacy six-call
budget (six times the Execution timeout in `build/farm/grpc-service-config.json`, currently
720 seconds). Scaling that deadline would no longer prove the box's production policy survives
the full legacy retry budget. We retain this cost once nightly, outside per-push check.

The job selects `aid-nightly-compute` on #1867's compute pool: its 6 GiB memory limit
accommodates the proof's 4 GiB requirement without assuming the default 2 GiB PR node
is sufficient. The compute pool now supplies an
11 GiB home / 12 GiB ephemeral ceiling; see [the nightly rollout](publish.md#the-nightly).
This changes no live node template or quota. The node supplies pinned Node and Bazelisk, and
`.bazelversion` selects Bazel. `ci/zuul/bazel-fallback.sh` uses a private checkout and Bazel output
root, ignores machine/user rc files and targets only loopback REAPI servers. Its 50-minute
watchdog sits inside the nightly job's inherited two-hour timeout, which bounds both the lab
and the full suite. No shared live farm is saturated or modified by the lab.

The job writes raw `remote-rpc.pb`, compact `execution.log` and `build-events.jsonl` outside the fetched
tree, in `~/zuul-bazel-diagnostics`. `ci/bazel-job.py` runs Bazel and then exports bounded test XML,
test logs and compressed diagnostics under `~/zuul-output/logs/bazel`, even after a nonzero exit.
It never exports OCI layouts, runfiles or the entire `bazel-testlogs` tree. The only undeclared
outputs exported are bounded Node crash JSON (`test.outputs/node-crash/{before,after,report.*}.json`);
cores, symlinks and arbitrary undeclared payloads stay excluded (#2040).
The byte/file/count/preview bounds have one source, `ci/bazel-log-budget.json`: currently at most
32 MiB including the manifest, with at most 512 recorded files in a fixed-depth tree. This leaves
room below the executor's independent 250 MB per-job default for checkout, Ansible and console logs;
it does not increase that limit or guarantee the size of unrelated executor files (#1893).
Small RPC/BEP logs are complete `.gz` files; decompress before decoding. The compact
`execution.log` contains zstd-compressed ExecLogEntry protobufs and is copied intact
only when it fits the per-file and remaining total budget. The measured cold full
log slightly exceeded 4 MiB and was omitted; do not assume it is fetched. Older
JSON-log callers remain supported by the general exporter. Oversized text gets
an explicitly marked head/tail preview; oversized binary/XML is omitted. `manifest.json` records
original sizes, paths and omission/preview status. An omitted trace is not evidence of zero actions.
The RPC log captures method/status/timestamps before the circuit breaker
can refuse later calls; the other logs distinguish actual remote execution, cached results and unrun
targets when retained. The trusted base fetches only `~/zuul-output/logs` in post-run. See the farm's
[RPC abort procedure](../../deploy/thunderbox/build-farm.md#an-rpc-circuit-breaker-abort)
for decoding, correlation and recovery requirements. A breaker abort is not a test pass or a test flake.
The BEP keeps local file paths rather than uploading referenced files through the failed farm; those
paths describe the build node and are not retained test-output attachments. A failure before execution
can leave an empty execution log, and interruption can leave partial logs; neither proves a target passed.

The full job overrides `--repository_cache=` to avoid keeping downloaded blobs
next to materialized repositories in a disposable home, and uses compact execution
logging instead of multi-GiB JSON finalization. The Bazel role indexes the read-only
shared SHA-256 downloads into `~/zuul-shared-distdir` using symlinks, and the full job
passes it as `--distdir`. Bazel verifies the digest before reading a candidate;
OCI URL names (`sha256:<hex>`) match these aliases. Seeded blobs require no blob/manifest
HTTP and take no duplicate payload space in home. rules_oci still requests an
uncacheable registry token first; `--repo_env=OCI_GET_TOKEN_ALLOW_FAIL=1` lets a
failed exchange (including 429) fall through to the digest-verified distdir read.
Unseeded blobs still fetch upstream, where the registry enforces authentication. Other jobs retain their seeded caches. See [cache seeding](oci-downloads.md). The [storage investigation](gate-compute-storage.md)
records the measured peak and the 170-test cold reproduction with the unchanged
3 GiB heap. This settings prerequisite leaves all nodesets and pod budgets unchanged;
compute routing stays held in #1926 until the orchestrator releases its activation.

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
whose node lacks what it needs fails: an image job checks its node's Bazel and endpoints, staging its Docker, the
ingress suite that `aid` finds the Swift runtime, before they start. Nothing passes for want of Docker.

| CI's queue run (`ci.yml`) | Gate's job | Planned when (`ci/affected.mjs`) | Node |
| --- | --- | --- | --- |
| the plan job's quarantine check and plan | `aid-gate-plan` | always | the gate compute node |
| `bazel`: `bazel test //...` | `aid-bazel-everything` | `bazel`: Swift inputs | the gate compute node, on the farm ([The full job](#the-full-job-on-a-build-node)) |
| `bazel`: the architecture's actual-edge check | `aid-bazel-graph`: `node ci/bazel-graph.mjs` | `bazel` | the gate compute node |
| `ingress`: the ingress suite | `aid-gate-ingress`: `aid` built on the farm, then [`ci/ingress-suite.sh`](../ingress-suite.sh) | `ingress`, whole or the applications' part | the gate compute node |
| `images`, one per application (`app-image.yml`) | `aid-gate-image-<app>`, one per `apps/<app>/ci/check-image.sh` | `images` names it | the gate's image node |
| `release-folder` and `aid-images` | `aid-gate-aid-images`: `deploy/release-folder.sh`, then AID's three images | `aid_images` | the gate's image node |
| `farm-worker` | `aid-gate-farm-worker`: `node ci/build-farm-worker.mjs build` | `farm_worker` | the gate's image node |
| `zuul-node-image` | `aid-gate-zuul-node-image`: `node ci/zuul-node-image.mjs build` | Zuul's infrastructure check | the gate's image node |
| `infra-checks` | `aid-gate-infra-build-farm`, `aid-gate-infra-zuul`: `ci/infra-check.sh <check>` | `infra_checks` names it | the gate compute node |
| `staging` (and its release folder) | `aid-gate-staging`: `deploy/release-folder.sh`, then `deploy/test/staging/run.sh` | `staging`: `VERSION` changed | the staging node |
| `quarantine-swift`, `quarantine-node` (not blocking) | `aid-gate-quarantine-swift`, `aid-gate-quarantine-node`, `voting: false` | a test is in quarantine | the gate compute node |

[`check.mjs`](check.mjs) holds gate's list in [`zuul.d/project.yaml`](zuul.d/project.yaml) to the plan's jobs
(`node ci/zuul/gate.mjs jobs`), each behind `aid-gate-plan`, on its node, and only the quarantine runs not voting.

**What is not carried over.** The queue's images kept for main (#466): post builds and checks each image again on the
merge ([`publish.md`](publish.md)), with the same commands ([`steps.sh`](steps.sh), which post's scripts and gate's
share). The release folder is built in each job that needs it, clean, rather than once for two jobs through the run's
Actions cache: no CI artifact or cache carries it between jobs (#1286). The quarantine runs' records are kept with the
build's logs (`quarantine-swift.json`, `quarantine-node.json`); `node ci/quarantine.mjs history` still reads only
GitHub Actions' artifacts. The cheap layer's steps (the Node suites, the CI and workflow policy tests) are check's
(#1520).

**The nodes.** The gate compute node
([`deploy/zuul/gate-compute-node/`](../../deploy/zuul/gate-compute-node/README.md)) runs the plan,
full Bazel build, graph, ingress, infrastructure and quarantine jobs. Nodeset `aid-gate-compute`
selects label `aid-gate-compute-pod-node`; its dedicated bounded provider admits one disposable pod.
Its PR-node image carries the Swift runtime libraries, without a compiler, for the farm-built `aid`
the ingress suite starts. Its projected `aid-build-farm` token uses exact subject
`system:serviceaccount:zuul-gate-compute:compute`, mapped to client `zuul-gate-compute`, tier `dev` only.
It holds no image-upload, image-check or publishing credentials. The activation/storage hold
[in The full job](#the-full-job-on-a-build-node) applies before any routing switch.

The PR node ([`deploy/zuul/node/`](../../deploy/zuul/node/README.md)) serves check using its own
check-provider slots and `zuul-pr`/dev identity; gate compute jobs no longer consume those slots.
The gate image node ([`deploy/zuul/gate-node/`](../../deploy/zuul/gate-node/README.md)) runs image jobs
on its separate single slot: the post node's image, the build farm, scratch registry upload access
and the image-check namespace, with no publishing secret. Its projected token uses exact subject
`system:serviceaccount:zuul-gate:gate`, mapped to `zuul-gate`/dev, while only trusted
`system:serviceaccount:zuul-post:post` maps to `zuul-post`/main. Each client identity is revoked
separately through `farm-revoked`.
[Prerequisite deployment](../../deploy/zuul/image-build-prerequisites/README.md) prepares these grants;
only the orchestrator releases the held switch after its live dependencies.
[Container images](../../build/images/README.md) lists the Bazel targets and remaining runtime checks. The staging node
([`deploy/zuul/staging-node/`](../../deploy/zuul/staging-node/README.md)) is Docker, for staging alone.
Every check/gate/post job runs in a fresh disposable pod. `gate.sh` creates job-local temporary work;
it does not inspect or terminate earlier processes, repair a reused state directory, or shut down a warm
Bazel server for the next job. The launcher deletes the pod after the job, including cancellation.
Image-check cleanup still deletes that build's separate scratch pods. Provisioning, proof and rollback follow
[disposable-nodes.md](disposable-nodes.md).

### Turning the gate's jobs on

The gate image node and provider `aid-k3s-gate` are live (orchestrator, 2026-11-16 09:20Z).
The staging node does not exist yet: a `VERSION` change cannot pass gate until Kristóf decides
its placement and the node is provided. Keep that job required for releases. The original
rollout procedure below records the dependencies; current cutover ownership and evidence are
in [cutover.md](cutover.md).

0. **Kristóf's decisions**: the gate's image node's 128 MiB of ThunderBox's node memory,
   beyond the node's cap as it stands; and where the staging node runs
   ([`deploy/zuul/staging-node/`](../../deploy/zuul/staging-node/README.md), "Undecided").
1. **The PR node's image** (the admin): the node image from a `main` with this change, which carries the Swift
   runtime, applied to the PR node ([`deploy/zuul/node/README.md`](../../deploy/zuul/node/README.md), "Applying it").
2. **The gate's image node** (the admin, after a heads-up to the orchestrator; ThunderBox's session
   for its grants): its README's "Applying it" and the [Bazel image rollout](../../build/images/README.md#rollout-after-merge).
3. **The staging node**, as step 0 decides.
4. **aid-zuul-config** (the orchestrator merges, after the other bot's review): the labels `aid-gate-pod-node` and
   `aid-staging-node`, with SSH host-key verification (staging pins its static key; disposable gate/post
   pods are scanned by the launcher). Drain and retire old static allocations before switching providers;
   see [disposable-nodes.md](disposable-nodes.md#provider-migration-prerequisite-1754). Check:
   `/api/tenant/aid/config-errors` is `[]`.
5. **A gate run** of an ordinary pull request at the switch: `aid-gate-plan`'s log names the planned jobs, they run,
   and the others are `SKIPPED`.

The testbed's `testbed/check-github-gate.py` and `testbed/scenario.py` exercise approval,
current-head handling, holding, timeouts, speculative ordering and regating independently
of production job coverage. Historical rollout evidence is in [comparison.md](comparison.md).

Node suites are Bazel tests (#1313): `aid-cheap-checks` selects `//:node_<suite>` and CI policy targets,
`aid-bazel-everything` includes them through `//...`, and `aid-gate-quarantine-node` invokes the manual uncached
`//:node_quarantine` action. The wrappers execute on the farm with pinned Node; results are Bazel-cached.
See [README.md](README.md#the-cheap-checks) for inputs, loopback requirements and report retrieval.

Flake recording (#726) keeps raw BEP and the full per-test ledger in
`~/zuul-flake-results`, outside fetch-output. Only the compact `logs/flake-results.json`
summary is copied back. Its limit is `perFileBytes` in `ci/bazel-log-budget.json`;
`ci/bazel-job.py` reserves that much of the same total budget for the sibling summary.
Overflow is explicit missing evidence, never a clean CI window.
