# Scratch registry retention (#1817)

This is the AIDepartment half of the accepted design. It is **not a live rollout**.
The orchestrator owns ThunderBox and trusted `aid-zuul-config`; neither is changed here.
`CI_SCRATCH_LIFECYCLE=1` activates owned naming/journaling/deletion. Until coordinated
activation, image naming and manifest retention retain their previous behavior. Gate
image jobs nevertheless share the single job-wide `aid-scratch-registry` semaphore.
They declare `resources-first: true`: Zuul fulfills the node request before acquiring
this semaphore, so waiting for a pod cannot lock out scratch consumers or maintenance
(#1939; [Zuul semaphore ordering](https://zuul-ci.org/docs/zuul/14.3.0/config/job.html#attr-job.semaphores.resources-first)).
Exclusion still covers execution through cleanup. An allocated node may wait for a
trusted post/maintenance holder; those jobs must not require the gate image node
while holding this semaphore (that would invert the acquisition order).

## Lifetime and ownership

An attempt owns `run-<zuul.build>-1/<repository>`. `zuul.build` is the **job execution**
UUID, not the buildset UUID; a retried job gets a new execution UUID. Gate supplies it
as `GITHUB_RUN_ID`; post already does so via `job-env.sh`. Attempt-private repository
links prevent deleting another job's identical manifest digest. The registry still
shares the content-addressed blobs; only global reachability GC may reclaim those.

The gate exports `CI_SCRATCH_STATE=$HOME/zuul-scratch/<build>`. Post stages share
`CI_SCRATCH_STATE=$ZUUL_STATE/scratch` (or an explicit trusted override). Do not put
it in stage-local tmp folders. Before a push, `scratchRepository` validates admission
and appends repository intent to `journal.jsonl`. Every successful builder immediately
appends the returned digest, including each success before a multi-image failure.
Cleanup discovers tags in these private repositories to cover a completed push whose
metadata never reached the client. It journals the complete reachable manifest graph
before deletion, including index children. A retry after deleting an index still finds
its now-untagged children. An absent manifest/repository is success; other HTTP errors
are reported, other repositories are still attempted, and the journal is retained.

Consumer lifetime includes probes, checks **and the completed pusher copy** in post.
Do not delete at the end of post's build stage: later playbooks still need those images.
Gate's cleanup `post-run` calls `ci/zuul/scratch-cleanup.sh <zuul.build>` on success, failure
or abort. EXIT traps are only an extra pod cleanup attempt. The script cleans check
pods, calls the operator's fence hook, then deletes manifests. It propagates errors.
Cleanup post-runs use the job's post-timeout and can be interrupted, so cleanup
success cannot be the only interlock: trusted admission MUST recover before the next
producer. Node loss may also prevent the cleanup post-run entirely.

## Operator hook contract

Install `/usr/local/libexec/aid-scratch-control` on image nodes, backed by trusted
maintenance authority (not a credential that lets untrusted PR code restart registry
pods). This path is fixed; no arbitrary command from an endpoints file is executed.
Its commands receive `CI_SCRATCH_REGISTRY`, `CI_SCRATCH_STATE`, `GITHUB_RUN_ID`,
`GITHUB_RUN_ATTEMPT=1`, and `CI_SCRATCH_LIFECYCLE=1` in their environment:

- `admit`: while the job holds `aid-scratch-registry`, close maintenance admission,
  fence leftover writers/consumers, recover orphaned private manifests and partial
  uploads, reclaim blobs, restore registry health/writes, and only then authorize
  this new attempt. Gate calls it before any image push. Trusted post pre-run must
  call it once before build (not again in push/record).
- `fence`: stop/cancel this attempt's BuildKit writers and wait until they cannot
  commit an upload, and stop/wait for its check/pusher consumers. It must not merely
  kill the SSH client or inspect an idle queue. Only then authorize manifest deletion.
  Gate cleanup post-run calls it; trusted post cleanup post-run uses the same script/hook.
  Repeated fencing must be idempotent. Failure leaves maintenance admission closed.

Each command atomically writes `$CI_SCRATCH_STATE/admission.json` only after success:

```json
{"version":1,"owner":"run-<32-lowercase-hex-build>-1","registry":"registry-scratch.arc-scratch.svc:5000","phase":"produce"}
```

`fence` writes `"phase":"cleanup"`. Owner, registry and phase must match exactly.
These receipts catch stale/wrong-job integration; they are **not a security boundary**
against malicious job code. Actual exclusion, authorization and fencing live in the
trusted controller and job semaphore. Do not infer safe GC from a receipt alone.
An expired lease, timed-out cleanup or lost node does not prove its BuildKit writer is
stopped. Keep durable maintenance failure state outside the disposable job pod.

## Required cross-repository changes and rollout order

1. **ThunderBox `ci/scratch-registry.yaml`:** enable manifest DELETE with
   `storage.delete.enabled: true`; keep sizeLimit within the scratch allocation from
   `externalMiB["arc-scratch"]` in
   [`deploy/zuul/scratch-allocation.json`](../../deploy/zuul/scratch-allocation.json)
   and preserve scratch-status monitoring; provide a safe stopped/readonly maintenance
   path and API access for the gate/post node clients to list tags, read and delete
   manifests in owned repos.
   Verify registry network policy permits these clients, not only their BuildKit pods.
2. **ThunderBox `ci/janitor.mjs`:** remove idle-read-then-restart authority from
   `rotate-scratch`. GC and fallback rotation must acquire the same exclusion and
   enforce durable admission/fencing. Reclaim failed/aborted jobs' referenced orphan
   manifests and partial uploads too: GC alone does not remove referenced manifests.
   A reset may be used only with all writers/consumers fenced, under the same slot.
   Never use automatic lease expiry as evidence that a producer is dead.
3. **ThunderBox `ci/janitor.yaml`:** replace the independent destructive CronJob path
   with an enqueue of trusted maintenance using the shared Zuul semaphore, or an
   equivalent controller obeying exactly the same admission mechanism. Keep age-based
   fallback and the status alert, but do not let a cron rollout bypass exclusion.
4. **Trusted `aid-zuul-config`:** all scratch-using post jobs and maintenance jobs must
   request the **same tenant** job semaphore `aid-scratch-registry` (max 1). AID declares
   it; do not define a conflicting second semaphore. Use job-wide semaphores, not a
   run-playbook-only lock. Post's trusted pre-run invokes `admit` with the durable state
   env above; its cleanup post-run (`cleanup: true`) invokes `ci/zuul/scratch-cleanup.sh <zuul.build>` with
   the same env, after all build/push/record playbooks. Ensure the node image carries
   the control hook and credentials needed only by the trusted controller. Inspect
   Zuul's effective inherited job: child cleanup post-run executes before parent cleanup.
   Do not place fencing only in a later parent cleanup post-run.
5. **Activation:** prepare the above from merged commits using normal operator rollout
   checks. Drain existing gate/post jobs and disable/drain every old Actions/manual
   scratch producer/consumer. Verify the complete producer inventory participates;
   semaphore protection does not cover clients outside Zuul. Enable lifecycle for
   gate in `/etc/aid-gate/endpoints.env` and for post in trusted playbook environments.
   Keep admission closed throughout the transition. No mixed legacy/new producers.
6. Before reopening admission, measure peak compressed images plus transient uploads,
   registry metadata and GC overhead for the largest **single post job** against the
   limit. The reported full gate estimate (~6750 MiB) is a planning input, not proof
   that every job fits. Run isolated interleaving/abort and two-distinct-buildset
   retention tests with the real controller/registry; retain the evidence. If a job
   cannot fit, subdivide it under the same lifetime discipline before activation.

Before each next producer, not just hourly, maintenance must reclaim the previous
job's storage. Serializing without that maintenance still fills the registry.
A cleanup timeout, failed DELETE, missing journal, or lost worker must be recovered
under exclusion before issuing the next produce receipt. If recovery fails, refuse
new pushes and alert; do not turn the failure into a best-effort warning.

## Validation boundaries

`ci/scratch-lifecycle.test.mjs` uses an isolated Distribution API double to exercise
private digest links, journal/discovery, multi-image failure, pagination, OCI graph
cleanup, partial-delete retry, 404/restart, and receipt/error handling. It also checks
the actual AID job wiring. Its interleaving/back-to-back storage test is explicitly a
**contract model**, not an integration proof of the operator's unimplemented semaphore
controller, fencing, GC or the live registry's byte budget. No production GC, restart,
DELETE or infrastructure apply is performed by these tests.

Zuul's upstream [job](https://zuul-ci.org/docs/zuul/latest/config/job.html) and
[semaphore](https://zuul-ci.org/docs/zuul/latest/config/semaphore.html) references define
job-wide acquisition and cleanup post-run behavior. The operator must additionally prove
the installed scheduler/executor version keeps the slot through cleanup and that a
crash/retry cannot bypass durable maintenance admission.
