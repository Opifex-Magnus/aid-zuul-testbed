# Error loops and lost check reports (#1855)

This is a **separate opt-in deployment**, not part of `server/`. The orchestrator applies it from
clean merged main, after reserving **32 MiB / 10m CPU** more on the CI node (128 MiB memory limit).
The previous node allocation has no spare capacity; do not apply until its owner has allocated this share.
Until this opt-in rollout is completed and recorded, the permanent 30-minute CI manifest heartbeat will
report `zuul-ci-observer` and `zuul-ci-observer-admin` as drift with missing apply history (exit 1). These
standing warnings are expected while the reservation is pending; they are not new incidents or a reason
to mute the heartbeat or fabricate apply records. The orchestrator must track this pending rollout explicitly.
No running farm or Zuul workload needs replacing for the observer itself. The farm Prometheus config
change replaces Prometheus and loses its emptyDir history, resetting pending alert timers.

The collector polls every two minutes, reads the last ten minutes of each running component's container
logs from Kubernetes, and exports one `ci_component_errors_10m{namespace,component}` gauge per component.
It recognizes standalone `ERROR`, structured `"level":"error"`, and Go glog `E1119 …` prefixes. Multiline
continuations do not count as additional errors. Components are enumerated in `observer.py`: Zuul scheduler,
launcher, web and executor; farm frontend, scheduler, storage, both worker pools, usage, portal and Prometheus.
It counts all containers and replicas of each component. No log bodies leave the collector or enter metrics.
A missing component, truncated window, denied log access or other failed scan reports unhealthy, never zero.
Buildbarn plain Go logs may describe failures without a severity marker (for example `Failed to …` or
`rpc error: code = …`); those lines are currently not counted. An INFO message containing `result ERROR`
can count. Do not interpret this literal severity matcher as complete failure coverage.
Kubernetes rotation/restarts can lose logs before collection: this is a bounded polling alarm, not a log archive.

For both repositories listed in `REPOS`, the collector pages through open PRs and the latest check runs on their
current heads. A queued/in-progress `aid/check` or `aid/gate` is stuck when Zuul's latest completed buildset for
that repository, SHA and pipeline has a final result and message, and its last build ended at or after the check's `started_at`.
A missing or null check start or final build end is treated as not stuck.
This excludes an old build on the same SHA when a fresh rerun is starting. Completed checks, old PR heads, closed
PRs and unrelated checks are ignored. Buildsets without any build end (for example no-job configurations) do not
qualify for this comparison. This collector does not repair checks or re-enqueue changes.

All reads have a ten-second timeout, a 4 MiB response ceiling and at most twenty 100-row GitHub pages. Exceeding
any bound raises the scan alarm. GitHub rate limits, missing credentials and a Zuul outage do the same. The
metrics endpoint returns 503 if no poll has finished in five minutes. No API response/error bodies are logged.

## Alerts and destination

The existing farm Prometheus scrapes `ci-observer.zuul.svc.cluster.local:9103` every thirty seconds:

- `CIComponentErrorRate`: **more than five errors in ten minutes**, continuously for **thirty minutes**,
  separately for every namespace/component. A steady error loop is detected within about forty minutes
  plus polling/scrape/Gatus delays, within the issue's hour.
- `CIStuckCheckRun`: one or more stuck runs continuously for five minutes.
- `CIObserverUnavailable`: failed scan, failed scrape or missing scrape target for five minutes.

`ci:observer_healthy` is zero when any of these alerts fires. The build-farm-ui `/health/ci` route reads it and
returns only a boolean; missing/stale Prometheus data returns 503. Install the **CI error rate and stuck checks**
monitor from `deploy/build-farm/gatus-monitors.json`. Existing `gatus-site-down` and
`gatus-site-up` automations in `../gatus-alerts.json` send warning/recovery notifications to the **human operator**
via AID `notify_user`, including their configured notification channels. There is no Alertmanager configured:
Prometheus's Alerts UI identifies the component; Gatus supplies the notification, rather than merely leaving a
firing rule in an unread UI. Existing orchestrators subscribed to Gatus events can use the same monitor.

## Rollout (orchestrator, after merge only)

1. Record the extra node reservation in the host's budget; measure RSS and poll duration on staging before the
   live rollout. Retain the normal tenant quota, and review the rendered requests and limits.
2. Use the dedicated **`kristofk-aid-ci-observer`** GitHub App (created **2026-11-20**,
   App ID **5841135**, installation **183860011**). It has no webhook and only **Checks: read**,
   **Pull requests: read** and **Metadata: read**, installed only on the two repositories in `REPOS`.
   No Contents permission is needed: the PR list supplies the head SHA. Never use Zuul's own App key
   or a classic PAT. Fine-grained PATs cannot read these private-repository check runs.
   From the operator box, create `zuul/ci-observer-github` with the three mounted keys below, piping
   the private key from AID's keychain without a temporary key file or terminal output:

   ```sh
   aid-keychain get ci-observer-github-app-key |
     kubectl -n zuul create secret generic ci-observer-github \
       --from-literal=app-id=5841135 --from-literal=installation-id=183860011 \
       --from-file=private-key.pem=/dev/stdin
   ```

   The key was created **2026-11-20**; record each rotation date. The observer uses the scheduler
   image's PyJWT to sign RS256 JWTs and exchange them for short-lived installation tokens, caching
   until five minutes before expiry. The pinned-runtime testbed runs its auth fixtures with real PyJWT.
   Missing keys, signing dependencies and failed mint/refresh requests report the checks scan unhealthy;
   no key, JWT, token or error response body is logged. Rotate the mounted key before revoking the old
   one; subsequent refreshes reread the secret. Restarting the observer forces an immediate mint.
3. Admin: use the recorded manifest wrapper for the multi-namespace `zuul-ci-observer-admin` set,
   **without `-n`**: `mise exec -C deploy/zuul -- node ../ci/manifests.mjs diff zuul-ci-observer-admin`,
   then the same command with `apply` after reviewing the diff. It grants only pod listing and log
   reads in three namespaces to the observer's identity; no node APIs, exec, secrets or mutation. The included
   NetworkPolicy permits the Kubernetes API/proxy and farm Prometheus ingress. Existing tenant base policies
   supply DNS, same-namespace Zuul web and outbound proxy access. Apply the farm's new `farm-ci-observer`
   egress NetworkPolicy from `deploy/build-farm/namespace/observability.yaml` as well.
4. Tenant: use `mise exec -C deploy/zuul -- node ../ci/manifests.mjs diff zuul-ci-observer`,
   then the same command with `apply` after reviewing the diff. The wrapper records the merged commit
   separately for observer/admin sets; do not replace it with an unrecorded bare apply. Both steps use the
   default admin SSH transport; they do not require a local kubectl context.
   The component uses `server/version` for the existing Python runtime image pin. No extra image build is needed.
5. Diff/apply only the farm observability ConfigMap/Prometheus changes from merged main following the farm
   runbook. Do not blindly apply unrelated execution ConfigMap changes. Publish/install build-farm-ui with the
   new `/health/ci` route, then add the Gatus monitor (replace `FARM_UI_HOST` with that installed app's Docker DNS).
6. Verify a live read-only scan with this dedicated App against **both** repositories: list open PRs,
   then GET `/repos/<repo>/commits/<head.sha>/check-runs?filter=latest` for a current PR head in each.
   Require HTTP 200 and record only repository, status and check names, never credentials or response bodies.
   The orchestrator recorded successful probes for both repositories on **2026-11-20** in #1919;
   repeat after rollout with the mounted credential.
   Require both `ci_observer_scan_ok` series to equal 1, all expected component series, and `up{job="ci-observer"}=1`.
   On staging, feed a component steady ERROR lines and leave a fixture check open after its finished buildset;
   record Prometheus pending/firing, Gatus `site.down` and AID notification, then recovery. For every farm
   component, compare a sanitized staging failure sample with its counter, including plain Go `Failed to …`
   and `rpc error: code = …` lines. Record any undercount on #1855 and open a follow-up issue for component-specific
   patterns with positive and negative fixtures before claiming full farm error coverage. Never interrupt a live
   build or alter a live check to demonstrate the alarm.

Undo: remove the Gatus monitor, restore Prometheus config/rules, remove only the observer Deployment, Service,
ConfigMap, ServiceAccount, Secret and the named Role/RoleBindings/NetworkPolicies from these manifests. Do not
remove existing namespace base policies, farm data or Zuul state. Release the extra reservation after removal.

## Checks

From the checkout root (all execution on the build farm):

```
mise exec -- bazelisk test //deploy/zuul/observability:rules_test --noremote_local_fallback
mise exec -- bazelisk test //:node_ci_ci-observer //:node_build-farm-ui --noremote_local_fallback
```

The first runs actual promtool fixtures for every component, threshold timing, stuck runs, scan failure and
recovery. Each alert separately exercises `ci:observer_healthy`: healthy and pending return 1, firing returns 0,
and recovery returns 1, using actual alert evaluation rather than synthetic `ALERTS` input. Its checksum-verified tool derives its version from `deploy/build-farm/mise.toml`.
The Node CI suite runs Python fixtures for parsing, current-head correlation, reruns, missing/truncated logs,
pagination and stale snapshots. UI tests check that the notification health route fails closed.
The buildset fields follow Zuul's pinned [14.3.0 BuildsetConverter](https://opendev.org/zuul/zuul/src/tag/14.3.0/zuul/web/__init__.py).
