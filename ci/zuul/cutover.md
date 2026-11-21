# Switching main to Zuul

This is #1355's cutover, owned by the orchestrator. Prepare and review the changes as a **held draft**;
only the orchestrator marks this PR ready or merges it after the prerequisites below are recorded.
The builder must not change live protection, disable integrations, or retire runners while preparing it.

## Before the switch

Record links to the live results on #1355. A closed issue alone is not evidence of a rollout.

- **Pending #1350, turn-on steps 0–7:** trusted publishing configuration loaded, the post node and pusher
  working, Pages serving `release-index`, and both an app image and a patch release published by Zuul.
- **Pending #1526:** reporters loaded without configuration errors and a real event received by AID.
- **Pending #1575:** three consecutive ordinary PRs with `aid-apps-lint-test` green under its timeout.
- **Done #1520 and #1521:** cheap checks merged in #1644; gate coverage merged in #1645, with
  `aid-zuul-config` #20 and #21. The orchestrator confirmed at 09:20Z on 2026-11-16 that the gate
  image node is live on ThunderBox and registered with Zuul as provider `aid-k3s-gate`.
  `aid-gate-zuul-node-image` checks both node images after the old workflow is removed.
  **Staging remains unavailable:** no staging node exists yet; its placement is Kristóf's decision
  ([`deploy/zuul/staging-node/README.md`](../../deploy/zuul/staging-node/README.md)). A `VERSION`
  change selects `aid-gate-staging` and cannot pass gate until that node exists. Other PRs leave
  staging out when their plan does not require it; no staging check is waived for a release.
- **Pending live protection, step 4:** the orchestrator makes `aid/check` required on main
  after disabling the old merge integration. This is a cutover step, not a prior coverage blocker.
- #1353 and #1354: comparison and recovery runbook complete; Bazel is the build of record (#1311).

## Ordered cutover

1. Stop new queue entries and drain running merges. Save the live branch protection, rulesets, integration
   settings, runner scale sets and image digests in the operator's private change record.
2. Confirm the prerequisites above. Keep this PR held until they are all met. Record its reviewed head.
3. Disable the old merge integration for this repository in GitHub's installed-app settings. Do not merely
   delete its configuration: an installed integration must no longer have authority to move main.
4. Apply `scripts/protect-main.sh` with an administrator account, then run its `--check`. Keep `hold` on
   this PR until the required `aid/check` is successful and the other bot approved this exact head.
   The orchestrator handles the one-time transition if the gate cannot merge the workflow-removal PR
   with its current App permissions; record the method and resulting commit. Never leave main unprotected.
5. Only the orchestrator releases this held draft. Confirm the first ordinary approved PR is tested by
   `aid/gate` and merged by `aid-zuul`, with stale approvals dismissed after a push.
6. Apply the build farm ConfigMap from this PR: `auth.libsonnet` refuses retired Actions tokens.
   Run `deploy/build-farm/check.sh` before applying and confirm box/Zuul access afterwards.
   Retire only the AID Actions runner scale sets after confirming no jobs are using them. Keep the
   Buildbarn farm, Zuul nodes, registry, pusher and image-check namespace. They serve Zuul
   independently of ARC. Follow the runner inventory below and record the actual host changes.

## Runner inventory

The former `swift` and `swift-image` ARC scale sets and their Actions registration are retired at the
switch. Their runner images, runner routing, cleanup workflows and Actions OIDC access are no longer
needed by AID. Generic ARC infrastructure belongs to its host repository: do not delete the controller,
shared namespaces or another repository's scale sets. The `arc-imagecheck`, `arc-scratch` and `arc-release`
namespace names do **not** mean those services are retired. Zuul publishing still uses them (#1350).
The build farm and its worker image are still required; they are not Actions runners (#1314).
Keep repository-wide Actions available for GitHub Pages' generated branch deployment; it has
no checked-in workflow YAML and builds or tests no AID code.

## Shared loop skills

The shared AID `work-on-issue` and `review-pr` skills were updated during preparation: draft/check
behavior comes from the repository docs, Zuul admission requires current-head approval and aid/check,
and a held cutover stays draft until the orchestrator releases it. Both retain the other bot's review
as independent verification. They live in AID, not this repository; no second copy is committed here.

Historical decisions and the dated comparison capture are retained as evidence, including their original
provider names/check titles. No active configuration or operating instruction depends on the retired queue.

## Acceptance and recovery

Observe ordinary merges for a week and one release on Zuul alone. Record their URLs, `aid/gate`
buildsets, merged heads, release/index digests and any failures on #1355. Do not dispatch measurement
runs or hold unrelated work to collect samples. The issue stays open until that evidence exists. The removal PR is **Part of #1355**, not a closing
keyword: merging preparation is not completion of the observation period.

If Zuul fails, follow `deploy/thunderbox/zuul.md`, “Merging when Zuul is down”. Only the documented
operator exception may bypass protection, and every exception is recorded. Restore the saved settings
and the old workflows from the pre-cutover commit only as an explicitly recorded rollback; never run
two publishers or two merge writers together. Reapply and check protection after recovery.
