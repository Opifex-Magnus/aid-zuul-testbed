# Separate gate compute pool (#1867)

This is a design for a coordinated follow-up implementation and rollout, not a
provider switch. Merging this document changes no job, quota, identity or live
infrastructure. The orchestrator owns the trusted-config companion and rollout.

## Implementation sizing after step 3

The #1867 implementation uses the orchestrator's 2026-11-19 post-step-3 decision:
reserve 384 MiB, not this design's initial 1024 MiB. Step 3 has three check pods,
three dev workers, 18048 MiB allocatable and a 120 GiB scratch dataset. The rendered
complete envelope with one compute slot is 17520/18048 MiB and 8335/9000m CPU after recorded BuildKit retirement, including the flake tracker reservation.
The initial activation evicted full Bazel at a 5 GiB home. The orchestrator's
revised decision is 11 GiB home / 12 GiB scratch, with both idle BuildKits retired
and no reduction to other tenants. The intermediate 15/16 GiB proposal exceeded
allocation; this revision saves 4096 MiB.
Scratch budget: **116256/116736 MiB**, **480 MiB spare**.
The remaining 528 MiB memory request headroom is not physical memory headroom.
Keep the 6 GiB limit and 3 GiB heap. Idle baseline and
activation stop criteria are recorded in `deploy/zuul/gate-compute-node/README.md`.
The snapshot arithmetic below remains the original design, superseded by those
rendered inputs for implementation. Additive prerequisites precede the separate
held routing PR; the orchestrator owns activation and trusted companion merge.

## Starting point and intended behavior

At AID `11717e9c`, check has two `aid-check-pod-node` slots in `zuul-nodes`.
Most non-image gate jobs inherit that nodeset through trusted `base`. Contrary
to the issue's original snapshot, `aid-bazel-everything` now explicitly selects
`aid-gate-image` (#1842): its 3 GiB JVM heap cannot fit the check pod's 2 GiB limit.
Splitting only the jobs that inherit `base` would leave the longest non-image
job contending with image builds.

Add one disposable **gate compute** slot. All gate jobs except scratch image
jobs and staging use it, including the plan job and `aid-bazel-everything`.
Check keeps its two slots, the image provider keeps one, the scratch semaphore
keeps max 1. The original design held the gate window at 1 until #1817; the
current trusted `aid-zuul-config/zuul.d/pipelines.yaml` sets window 2, floor 1,
ceiling 2 (2026-11-20, #1995). This does not add provider slots or increase the
scratch semaphore. This isolates
launcher admission; shared farm workers, executor capacity and host pressure
can still delay checks. It does not promise faster farm execution.

One compute slot serializes the plan and all seven subsequent non-image jobs,
including the up-to-two-hour full Bazel job. Previously, other non-image jobs
could use both check slots while full Bazel used the image slot. Check gains
isolation and image jobs lose that competitor, but the non-image gate critical
path can become the sum of these jobs. Measure gate wall-clock before and after
the switch alongside check latency; accept that trade-off explicitly before
activation rather than increasing pool size outside the node budget.

## Identities and containment

Use new names throughout; never reuse a static or image-provider identity:

- connection: `aid-gate-compute-pods`;
- section/provider: `aid-k3s-gate-compute-pods`;
- label/image/flavor: `aid-gate-compute-pod-node`;
- namespace: `zuul-gate-compute`, PodTemplate: `job-node`;
- ServiceAccount: `compute`; nodeset: `aid-gate-compute`, node: `node`.

Add `deploy/zuul/gate-compute-node/` using the **PR** node image at a checked,
pinned digest, not the post image. Copy the check template's restricted security
context, read-only root, private home/tmp, SSH readiness, read-only downloads
and projected `aid-build-farm` audience token. Keep all action execution remote.
Do not mount BuildKit, registry, publishing or image-check credentials.

The namespace admits one pod, with these container requests and limits:

```yaml
resources:
  requests: {cpu: 100m, memory: 1Gi, ephemeral-storage: 6Gi}
  limits: {cpu: "3", memory: 6Gi, ephemeral-storage: 6Gi}
```

Its unscoped `budget` ResourceQuota has `pods: "1"`, `requests.cpu: 100m`,
`limits.cpu: "3"`, `requests.memory: 1Gi`, `limits.memory: 6Gi`, and both
storage requests/limits at `6Gi`. Bound services, NodePorts and LoadBalancers at
zero; permit one downloads PVC with `requests.storage: 10Gi`. Copy check's
scratch LimitRange defaults (16Mi request, 128Mi limit) for future containers.
Keep the private home's 5Gi and tmp's 512Mi sizeLimits; logs/writable layers
share the remaining pod allowance. The 6Gi scratch ceiling is an explicit
constraint to prove with the full OCI graph, not permission to grow storage.

Make a new prebound ReadOnlyMany PV `zuul-gate-compute-bazel-downloads` and PVC
`bazel-downloads` pointing to the existing downloads-only host cache. This
adds a claim, not another 10Gi of scratch. Never share a writable cache or home.
Install namespace-local SSH host material with the existing executor login
public key; preserve the executor's current private login Secret. Do not run a
copied `node/secrets.sh` that rotates that shared login key.

Copy check's deny-by-default NetworkPolicy, admitting only executor SSH/console,
launcher SSH, DNS, the egress proxy and farm frontend. Extend the executor's and
launcher's destination allowlists for the new namespace. Grant the launcher
only pod get/list/watch/create/delete and get on this `PodTemplate/job-node`,
as in check's Role/RoleBinding. Extend `bounded.py`'s namespace allowlist and its
pinned-driver tests; keep confirmed deletion before slot release and `reuse: false`.

Add a distinct farm client `zuul-gate-compute`, exact subject
`system:serviceaccount:zuul-gate-compute:compute`, tier `dev` only, to
`deploy/build-farm/server/config/clients.libsonnet` and `auth.libsonnet`.
Use the existing Kubernetes token verification path. Test the exact subject,
rejection of default/wrong-namespace accounts, denied `main`, and independent
revocation through `farm-revoked`. Do not broaden the existing `zuul-pr` or
`zuul-gate` subject match. Include this client in any derived policy/UI inventories.

## Whole-node accounting

The inputs remain `node-allocation.json`, rendered workload requests and
`scratch-allocation.json`; the following arithmetic describes AID `11717e9c`,
not a fresh measurement of the host. Recompute it in the implementation.

At `11717e9c`, reservations were 15360/17024 MiB and 7080/9000m CPU, including all
three farm slots, two check slots, the existing image/post pools, both BuildKits,
Zuul services, overlapping janitors and the full shared image-check quota.
One compute slot adds 1024 MiB / 100m: **16384/17024 MiB and 7180/9000m**, leaving
640 MiB / 1820m unreserved. Count this even when the pool is idle. Do not subtract
the image node: image jobs still require it and may overlap compute jobs.

At that revision, scratch reserved 88592/97280 MiB. The new namespace's 6144 MiB makes
**94736/97280 MiB**, leaving 2544 MiB. Keep the dataset at 100GiB and every
existing namespace's storage ceiling. Copying the image pool's 16Gi allowance
would instead require 104976 MiB and fail admission. Two compute slots at the
proposed size would exceed both memory requests and scratch: provider and
namespace quotas must remain one.

The 6Gi memory limit preserves #1842's 3Gi heap plus 3Gi outside the heap.
The 1Gi request deliberately reserves more of the full Bazel client's 3Gi heap
than its siblings' 128Mi requests: it accounts for 1024Mi of the current 1664Mi
unreserved capacity and leaves 640Mi. This is a conservative initial capacity
choice, not a measured working-set claim; the activation proof must validate it.
The 1Gi memory request is a scheduling reservation, not six GiB of guaranteed
physical headroom. This remains Burstable, alongside existing burstable nodes.
Activation requires a live host-pressure audit and a full graph run in the new
6Gi scratch envelope. Stop on OOM, eviction, disk/memory pressure or the runbook's
under-4GB host memory condition; do not quietly raise limits, decrease other
requests or shrink the JVM heap. If proof fails, keep existing routing and record
an explicit revised capacity decision with the orchestrator.

Add `zuul-gate-compute` to both calculators' required tenant inventories; add its
render to `node-budget.sh`, manifest sets, deployment checks and live scratch
audit. Regression coverage must reject an omitted namespace/quota, N=2,
16Gi scratch, and insufficient capacity; positive coverage must calculate the
new totals from rendered resources and the existing allocation sources rather
than duplicating these snapshot numbers as future fixed constants.

## Trusted aid-zuul-config changes

These are reviewed companion changes, not a manual change to the running layout.
In AID's `deploy/zuul/server/config/zuul.conf`, add this admin-installed
connection alongside existing pods:

```ini
[connection "aid-gate-compute-pods"]
driver=kubernetes
namespace=zuul-gate-compute
```

Use the existing connections' authentication/context settings as well. Add the
connection to the launcher config tests; roll services that consume `zuul.conf`
from merged main through the existing runbook.

Append to trusted `zuul.d/providers.yaml`:

```yaml
- image:
    name: aid-gate-compute-pod-node
    type: cloud
- flavor:
    name: aid-gate-compute-pod-node
- label:
    name: aid-gate-compute-pod-node
    image: aid-gate-compute-pod-node
    flavor: aid-gate-compute-pod-node
- section:
    name: aid-k3s-gate-compute-pods
    connection: aid-gate-compute-pods
    launch-timeout: 300
    launch-attempts: 1
    flavors:
      - name: aid-gate-compute-pod-node
- provider:
    name: aid-k3s-gate-compute-pods
    section: aid-k3s-gate-compute-pods
    resource-limits: {pods: 1}
    labels:
      - name: aid-gate-compute-pod-node
        kind: pod
        reuse: false
        slots: 1
        spec: {}
    images:
      - name: aid-gate-compute-pod-node
        connection-type: ssh
        connection-port: 2222
        python-path: /usr/bin/python3
        username: zuul
```

Leave trusted `base`'s default check nodeset alone, along with its credential
and external-PR guards. Preserve the separate #1862 check-provider change to
two slots (trusted main still showed one during this design inspection).
No change to image/post providers, scratch semaphore or gate window is part of
this switch. Update trusted provider/config regressions for the additional
connection, exact label, fixed namespace, one slot and non-reuse.

## AID routing changes for implementation

Declare `aid-gate-compute` with `node` on `aid-gate-compute-pod-node` in
`ci/zuul/zuul.d/jobs.yaml`. Set it on abstract `aid-gate`, so plan, graph,
ingress, infra and quarantine jobs inherit it. Set it explicitly on
`aid-bazel-everything` too, replacing `aid-gate-image` and retaining its startup
heap override and remote-only Bazel flags. Each existing image job already has
an explicit `aid-gate-image` override; keep those and staging's `aid-staging`.
Do not change `aid-script`/`aid-bazel`: check jobs use them too.

Update `ci/zuul/check.mjs` and mutation tests to resolve inherited nodesets and
require compute for every gate non-image job, including plan and full Bazel;
reject fallback to check. Keep requiring image/staging overrides for those
jobs and the existing voting/dependency rules. Derive the gate inventory from
`gateJobs`, not a second fixed job list. Update the Compose testbed provider
fixture and sync/acceptance path to supply a genuinely separate compute node;
relabeling one of its check nodes would conceal contention.

## Merge, activation, proof and rollback

1. Implement the additive AID prerequisites and trusted companion under review.
   Keep routing in a separate held PR: current gate must still work until the
   new pool is proven. Run only affected Bazel targets on the farm, deployment
   lint/budget checks, trusted layout/provider tests and seam lint. Do not apply
   shared infrastructure from a proposed commit.
2. After prerequisites merge, the orchestrator uses a clean merged-main checkout,
   audits live allocatable, external reservations, scratch quotas, host memory/PSI
   and OOM/restart counters, and checks the complete rendered budget. Diff all
   new namespace resources at the checked PR-image digest. Establish the new
   exact farm identity, cache binding, SSH material, policies, RBAC, adapter and
   connection before enabling the provider. Apply starts no job pod.
3. Merge/reconfigure the additive trusted provider, verify its loaded layout and
   run an isolated canary using the new label. Confirm SSH host-key scanning,
   farm dev access, denied main and confirmed pod deletion on success/abort.
   Then release the held routing PR. Do not alias old labels or retire existing
   providers: old queued layouts may finish on their existing nodes.
4. Prove a full `aid-bazel-everything` run with its unchanged 3Gi heap, remote
   execution, logs and exit status inside the new memory/storage limits.
   During a long compute job, record two concurrent check build IDs and fresh
   pod UIDs in `zuul-nodes`; check requests must not be waiting on the compute
   provider. Confirm image jobs still allocate `zuul-gate` with one slot and
   staging uses its VM. Record before/after gate wall-clock and check queue latency for comparable
   workloads, namespace resource peaks, pressure, all terminal
   pod deletions and unchanged window/semaphore values. A static budget pass
   alone does not establish this outcome.
5. On failure, pause new admissions, drain owners and requests, then restore
   routing with a reviewed merged rollback (full Bazel back to image, other
   non-image jobs back to check). Disable the new provider only after its
   requests and allocations have drained; remove namespace/RBAC/client only
   when no owner remains. Never bulk-delete another build's pods or ZooKeeper
   allocations. Leaving idle additive prerequisites installed is safer while
   recovering; one slot must still be included in budgets until disabled.

This issue's deliverable is the design above. Implementation and live activation
need the reviewed companion changes and the recorded proof; neither is claimed
by merging this document.
