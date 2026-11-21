# Disposable job nodes

Issue #1698 migrates reused check, gate-image and post SSH nodes to one fresh pod per job.
The additive prerequisites (#1731/#1702) and trusted provider switch (cfg#30) are live.
The identities below are the #1754 replacement, paired with aid-zuul-config#31; cfg#30
reused the retired static names. Activation still requires the coordinated drain below.
The launcher creates a new pod on demand for each job, waits for SSH readiness and deletes it after the job, including a failed, timed-out or aborted job.
It waits for deletion before releasing the slot. No home directory, Bazel server or job process is reused. The
Docker staging VM retains its existing reset before and after every job; its label stays `aid-staging-node`.

## Provisioning and trust

Zuul 14.3.0's stock Kubernetes driver creates a namespace, ServiceAccount, token Secret, Role and RoleBinding for
every node. It cannot use a preconfigured namespace. AID instead loads a small fixed-namespace adapter in the
launcher only, [`bounded.py`](../../deploy/zuul/server/launcher/bounded.py). It keeps the upstream Kubernetes
provider, node serialization, quota accounting, allocation, SSH nodescan and leak collector. Its create/delete
state machines manage pods in the connection's fixed namespace, never namespaces or RBAC. The entrypoint refuses
other Zuul versions until this compatibility has been rechecked.

| Connection | Namespace | Job label | Account | Pod budget |
| --- | --- | --- | --- | --- |
| `aid-check-pods` | `zuul-nodes` | `aid-check-pod-node` | `pr` | 2 CPUs, 2 GiB RAM, 6 GiB ephemeral storage |
| `aid-gate-pods` | `zuul-gate` | `aid-gate-pod-node` | `gate` | 3 CPUs, 6 GiB RAM, 16 GiB ephemeral storage |
| `aid-post-pods` | `zuul-post` | `aid-post-pod-node` | `post` | 3 CPUs, 6 GiB RAM, 16 GiB ephemeral storage |

The orchestrator installs each namespace's `PodTemplate/job-node` from clean merged main with
`deploy/zuul/apply-disposable.sh <node|gate-node|post-node> <checked-image>@sha256:<digest>`.
The per-node `apply.sh <checked-image>@sha256:<digest>` commands delegate to this same installer.
Applying a template starts no pod. The launcher reads that template for every launch; the provider's `spec: {}`
is only a schema placeholder and cannot override it. The template supplies the image, security context, mounts,
labels, resources and identity. PR code can define jobs but cannot change the deployed template or provider.

The launcher's ServiceAccount has pod get/list/watch/create/delete and get on `PodTemplate/job-node` in those
three namespaces. It cannot read Secrets, modify policy/templates/RBAC or create namespaces. Its admin-installed
NetworkPolicy permits the k3s API route, in addition to existing DNS/proxy/node routes. Jobs receive none of the
launcher's token. Check's existing projected farm identity stays `zuul-nodes/pr`, and only reaches `dev`; gate/post
retain their separate image-check accounts, main/dev farm identities and network policies. `check-external` remains blocked
by the trusted base guard. The launcher explicitly enables host-key checking when constructing each real Kubernetes provider label: upstream
disables it for kubectl. The SSH nodescan must succeed and populate executor known_hosts before a job starts.

The check provider has `resource-limits: {pods: 2}`; gate-image and post retain `resource-limits: {pods: 1}`.
All use `reuse: false`. Each namespace quota admits that many pods and their summed requests/limits.
Check shares its two slots with gate jobs that inherit the base nodeset. Excess work waits in Zuul; no warm spare pod consumes the slot. Readiness checks the pod's
SSH probe. A launch retry adopts only a pod with the same Zuul identity. A failed or interrupted create is removable
by its deterministic name, even if the API succeeded before ZooKeeper saved the response. Pod tags let the upstream
leak collector remove abandoned allocations after a launcher outage. API failures are logged and retried. Even Zuul's delete timeout retains the allocation; only a confirmed API 404 releases its slot.

A ResourceQuota admission rejection (`403 Forbidden`, `exceeded quota:`) leaves the create state at
`START`: the launcher rechecks its configured N-slot limit before every attempt. Retained deleting
allocations continue to count until a pod read confirms `404`. If the API still reports full quota
because of an unmanaged pod or delayed quota reconciliation, admission retries at most once every
five seconds, without consuming the launch timeout or failing the request. Restarting the launcher
resumes this wait. RBAC, policy denials and other API errors still fail visibly; this does not change
quotas or delete other occupants to make room.

The proposed separate gate non-image pool, its capacity constraints and coordinated
trusted-config rollout are in [gate-compute-design.md](gate-compute-design.md) (#1867).
That design is not yet an activated provider.

## Provider migration prerequisite (#1754)

Scaling a static StatefulSet to zero does not retire its Zuul allocations. cfg#30 reused static
provider names and labels; a retained `StaticProviderNode` could satisfy a new request and then
reach the Kubernetes delete adapter. Check and gate have the same collision as post.

The replacement identities are `aid-k3s-check-pods` / `aid-check-pod-node`,
`aid-k3s-gate-pods` / `aid-gate-pod-node`, and `aid-k3s-post-pods` / `aid-post-pod-node`.
Never attach the old labels as aliases, or keep an old canonical provider name for a different
driver. Staging retains its static provider and label. Connections and PodTemplates do not change.

Before switching a provider, the orchestrator must pause admission, drain its jobs/requests and
retire its static allocations through Zuul's coordination APIs with the static lifecycle. Confirm
there is no executor/build owner and no static pod before retiring a node; a stopped controller,
SQL final result or missing log alone is insufficient. Save exact request/node identities and
states, lock and refresh them, and recheck ownership under the lock. Release only a terminal
request's reference to that node and remove only that drained static allocation. Use the pinned
static driver path, never Kubernetes `Delete`, a guessed `np<uuid>` delete or bulk ZooKeeper
removal. If an owner or unexpected state appears, stop and investigate. The admin REST wrapper
supports enqueue/dequeue, not node retirement; do not mistake a dequeue for completed cleanup.

For this incident, inspect request `7999decf6f0d4eb79c75bbefd9c86f74` and static node
`ea2cfca526345d849ac3e0cef1281fa1`, provider
`github.com%2Fkristofk%2Faid-zuul-config/aid-k3s-post`, label `aid-post-node`, address
`post-node-0.post-node.zuul-post.svc.cluster.local:2222`. Do not touch release node
`56e4bb1cacbf40a08dddab1199d60dc3`, subsequent disposable pods, or #1493's smoke records.
Audit check/gate for their corresponding retired providers (`aid-k3s`, `aid-k3s-gate`) and
labels (`aid-node`, `aid-gate-node`) using the same ownership checks; no wildcard cleanup.

Keep both companion PRs held until the orchestrator schedules the drain. Merge the trusted
config first, verify the loaded layout and new base/post labels, then release the AID PR to gate:
its `aid-gate-image` nodeset needs the new gate label. Do not admit old AID gate-image jobs
between these merges. The check job can run while AID remains draft. Config's provider regression
must pass against this AID checkout; the Compose fixtures use the new labels too. Apply any
shared infrastructure only from clean merged main. No change to the one-pod quotas is needed.

After the switch and stale allocation retirement, recover the original missed push using the
[authenticated admin wrapper](../../deploy/zuul/admin-api.sh):

```sh
kubectl -n zuul exec -i deploy/web -- sh -s orchestrator enqueue-ref \
  --tenant aid --pipeline post --project kristofk/AIDepartment --ref refs/heads/main \
  --oldrev f7e983839c473149fa83460109e469a7061ae545 \
  --newrev de08c4ec8b1b9aaf2607750efa617493912ecb8e < deploy/zuul/admin-api.sh
```

This reruns all post jobs for that range. Record `aid-publish-apps`' request/build URL, new provider
and label, fresh `np…` pod name/UID and its confirmed deletion after publishing. A later merge's
post range or a successful "Nothing to publish" job does not recover the missed app changes.
Record successful check/gate allocation and deletion too. This runbook does not claim live recovery.

## Caches and resource limits

`prepare-workspace-git` gets the speculative source from the executor's shared repository cache into the fresh pod.
No job modifies the executor cache. Buildbarn's existing action cache and trusted/untrusted instance split remain.

A shared host-backed cache at `/var/lib/zuul-downloads/repository-cache` contains **downloads only**, from approved
main builds. The three prebound `ReadOnlyMany` claims mount it at `/var/cache/bazel`, read-only. At node startup,
each SHA-256 hash directory is created privately and its existing files are symlinked to the share. Hits read the
share; misses and new canonical-ID markers write in the private directory, including when content has the same hash. Jobs cannot populate or poison the share. No job writes to another job's
home or promotes private entries automatically. Gate/post BuildKit continue to have their separate bounded caches.

The PodTemplate is the source of a node's size. Kubernetes applies those limits to the pod cgroup;
Bazel's container-aware JVM and the merged #1695 checkout settings derive heap, CPU, memory and local
test concurrency from that budget. The node wrapper preserves arguments without overriding resources.
The system rc selects the private download cache. Remote `--jobs` stays the farm's concurrency.
The whole-node calculator reserves one full slot per PodTemplate and requires its one-pod quota;
gate/post retain main's 25m idle CPU requests. This rollout adds no slot or memory request.

## Coordinated rollout

The orchestrator applies shared infrastructure **only from clean commits already merged into each repository's
main**, following [`deploy/thunderbox/zuul.md`](../../deploy/thunderbox/zuul.md). Before merge, use an isolated lab;
this PR is not authorization to apply to live check, gate, post or dev workers.

1. The additive prerequisites (#1731/#1702) were merged, published and applied before cfg#30.
   The orchestrator verified template digests, identities, RBAC, read-only caches and quotas, drained the
   static nodes and switched the trusted providers. The static definitions are now removed from source.
2. The orchestrator explicitly authorized #1749 to remove the retired StatefulSets/Services and reused-node
   cleanup immediately after the switch. Its own runs provide the live proof. Before marking it ready,
   record in its PR body a successful **own check job** URL, disposable namespace/name/UID, creation time,
   and confirmed deletion time/evidence. A Node test or template render is not live lifecycle evidence.
3. Opifex reviews the exact head, affected tests and `aid/check` pass, and normal Zuul gate owns merge.
   Record the successful gate job/build URLs and fresh pod creation/deletion. After merge, record a successful
   post publication on a fresh post pod and its deletion on #1698. Preserve cancellation/quota proof too:
   an aborted job's pod disappears before a different UID is allocated; excess work queues in the single slot.
4. Only the orchestrator applies this merged-main removal. Source deletion and `kubectl apply` do not prune
   old live resources. With the static controllers already at zero and providers disposable, explicitly delete
   only the retired resources: `statefulset/node` and `service/node` in `zuul-nodes`, `statefulset/gate-node`
   and `service/gate-node` in `zuul-gate`, and `statefulset/post-node` and `service/post-node` in `zuul-post`.
   Check they have no active static pods first. Keep namespaces, accounts, SSH Secrets, endpoint ConfigMaps,
   launcher RBAC, PodTemplates, download claims, BuildKit and host network/image-check grants. Apply updated
   templates/quotas only from clean merged main with checked published digests; never apply placeholders.

The removal PR carries `Closes #1698` as ordered. Record post/lifecycle evidence even if GitHub closes the issue
on merge; merge alone does not prove successful post publication. The orchestrator owns any live recovery.

Rollback requires a reviewed, merged rollback commit restoring static resources and cleanup plus the matching
trusted static providers. Pause/drain disposable jobs and wait for their pods to disappear before restoring a
static pod in the same one-pod slot. Never raise the quota or reuse a disposable pod/home. Keep the download
claims until the final disposable pod is gone; do not delete identities or host grants as a shortcut.

## Checks

`(cd deploy/zuul && mise exec -- ./check.sh)` renders/schema-checks the templates, checks budgets and RBAC,
and runs the existing deployment tests. `node --test ci/zuul-node.test.mjs ci/zuul-gate.test.mjs` exercises the node
wrapper/cache setup and gate execution. With the pinned Zuul runtime installed:

```sh
python deploy/zuul/server/launcher/test_bounded.py
```

The required OCI node-image runtime probe runs `image/test-download-cache.py` as UID 10001 with
its checksum-pinned installed Bazel, before publication. The image carries the checkout-shaped fixture
under `/opt/zuul-node-check`; it needs only loopback, using local stubs for unused toolchain modules
and an empty file registry instead of external module metadata. arc-imagecheck keeps its closed egress policy.
The probe verifies shared hits without HTTP, a successful same-hash/new-canonical-ID loopback download
and an unchanged read-only share. `//build/images:download_cache_test` runs the same fixture with real
Bazel and unusable outbound proxy settings on the farm. The Node policy suite covers the image wiring
and the fast cache-layout behavior. Both gate and post checks still require actual image runtime proof.

The Compose testbed runs the same lifecycle suite inside its pinned scheduler image before its ordinary scenarios.
Those scenarios still substitute static Compose nodes; they do not claim to prove Kubernetes admission or actual
pod termination. The coordinated rollout's gate/post and cancellation evidence is required separately.
