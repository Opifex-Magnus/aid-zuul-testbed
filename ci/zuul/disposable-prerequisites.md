# Disposable-node prerequisites (#1698)

This page records the additive #1731/#1702 bootstrap sequence. Those prerequisites and cfg#30 are now live.
Current template updates, static-resource removal, lifecycle proof and reviewed rollback follow
[disposable-nodes.md](disposable-nodes.md). The historical preservation steps below do not reinstall static nodes.

This additive deployment (#1731) is the first prerequisite for AID #1702. It registers `aid-check-pods`,
`aid-gate-pods` and `aid-post-pods`, installs the pinned Zuul 14.3 bounded launcher adapter,
and grants its dedicated ServiceAccount pod lifecycle/template-read rights in the existing
`zuul-nodes`, `zuul-gate` and `zuul-post` namespaces. The launcher uses its projected
ServiceAccount token, existing ZooKeeper TLS Secret and existing GitHub App Secret. No
credential is committed or copied into job pods. The admin NetworkPolicy grants its API route.

Existing StatefulSets, Services, node images, quotas, caches and static providers remain
active. No PodTemplate is installed and no provider is switched by this prerequisite PR.
Config PR kristofk/aid-zuul-config#30 stays draft and held until the orchestrator explicitly
releases it after the prerequisites are live. AID #1702 adds the unused templates/cache path
while preserving static nodes and cleanup. It gates on static providers before cfg#30 is
activated; only a later proof-backed follow-up removes static pieces.

Only the orchestrator applies this change after ordinary review and merge, from a clean
merged-main checkout, following `deploy/thunderbox/zuul.md`. Before applying, drain work
and inspect the rendered diff. Apply the additive namespace policy, the three namespaced
launcher Roles/RoleBindings, and the server configuration/launcher. Preserve all deployed
node image digests; do not apply node image placeholders. Check server readiness and
`/api/components`, verify the three connections are registered and the existing static
providers still work, and verify launcher authorization (pod lifecycle and job-node
PodTemplate read only; no Secret, namespace, RBAC or template write access).

Record the merged commit and deployment evidence on #1698. Next, after explicit maintenance
release, #1702 must pass normal gate on static nodes, merge,
and have its templates/cache prerequisites applied and verified from main. Only then may the
orchestrator recheck/release config #30 and activate disposable providers. The full order is in
[disposable-nodes.md](disposable-nodes.md). Passing live gate/post,
fresh pod UID/deletion, cancellation and one-slot quota evidence belongs to the later
activation and remains required before closing #1698. It is not a prerequisite to merging
this additive deployment. No pre-merge host apply is authorized.

Rollback uses a reviewed merged rollback commit restoring the previous server launcher
and configuration. Preserve the static providers and node controllers throughout.

Checks: `(cd deploy/zuul && mise exec -- ./check.sh)` and
`python deploy/zuul/server/launcher/test_bounded.py` with full `zuul==14.3.0`.
