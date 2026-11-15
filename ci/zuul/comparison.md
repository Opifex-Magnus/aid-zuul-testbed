# Comparing Zuul with Actions and Mergify

Issue [#1353](https://github.com/kristofk/AIDepartment/issues/1353) compares the ordinary work already run by both systems. Kristóf's instruction on that issue
(2026-11-14) removes the dedicated measurement period: do not hold work, dispatch additional runs, or wait a week
to collect more samples. Actions and Mergify remain the gate until [#1355](https://github.com/kristofk/AIDepartment/issues/1355).

Compare a pull request **and its exact head SHA**, including superseded heads. A successful lint-only `aid/check`
does not establish parity with Actions' whole cheap layer. Keep missing work, missing reports, and differing
results visible. File each unexplained difference separately and link existing issues rather than duplicating them.

For reporting time, record the trigger and completion of each system's run, including waiting for a node. A draft
runs Zuul's check immediately but no Actions work until ready; their completion timestamps alone do not measure
executor speed. For approval to merge, use an approval of the merged head, and distinguish an observed merge from
a hypothetical gate result. With no production gate buildset, the latter is **unmeasured**.

The trusted gate configuration has a GitHub success reporter with `merge: true`; it is not a reporting-only gate.
Branch protection's `Every package passed` keeps ordinary pull requests out of its dependent queue (see
[gate.md](gate.md)). Do not remove that protection to obtain measurements. No gate buildsets means no evidence
of speculative full-job parity or a Zuul approval-to-merge time.

The results, source queries, and limitations of the existing runs follow below.

## What the data says

**This is not a parity sign-off for [#1355](https://github.com/kristofk/AIDepartment/issues/1355).** Sixteen ordinary PR heads have completed reports from both systems:
12 agree and four disagree. Zuul's check covers fewer jobs, so agreement says only that the reported outcomes
match. There is no production gate timing to compare. A missing week of measurements is accepted by the owner's
instruction; missing jobs, failing checks and the broken live rollout are not evidence of equivalence.

Actions and Mergify continue to gate work. No build, test-bed run, recheck, regate or workflow was dispatched to
produce this report. No merge control, job, trusted configuration or deployment was changed for it.

### Scope and source data

The [snapshot](comparison-2026-11-15.json) contains the exact SHAs, source report URLs, timestamps, per-job
outcomes, the merged cohort and the live configuration errors. History was read at **2026-11-15 01:41:23Z**;
live status/configuration at **01:46:11Z**. The report was assembled at **01:49:16Z**. This is a finite snapshot,
not a watcher, and later ordinary runs may differ.

- The retained Zuul history has **82 buildsets**, with triggers from **2026-11-14 10:16:51Z** through
  **2026-11-15 00:21:28Z**. Eleven belong to aid-zuul-config and are excluded from AID's comparison.
- AID has **71 buildsets**. Nine are existing rollout/diagnostic probes on [#1473](https://github.com/kristofk/AIDepartment/pull/1473), [#1494](https://github.com/kristofk/AIDepartment/pull/1494) and [#1499](https://github.com/kristofk/AIDepartment/pull/1499); they remain
  labelled in the snapshot and are excluded from the ordinary-work totals. This report created none of them.
- The remaining **62 ordinary buildsets** are 59 `check` and three `hold` buildsets. `check` has 19 successes,
  four failures, 34 dequeues and two merge conflicts across **56 distinct heads**. Reconfiguration can enqueue
  the same head again: those attempts remain visible and are not counted as independent heads.
- Of the 25 terminal ordinary check heads (success, failure or merge conflict), **16** also have a completed
  Actions `Cheap checks passed` report. The other nine have no such completed report; many are draft heads.
  The 34 dequeues are not passes. The source snapshot preserves their records instead of dropping them.
- The merged cohort since **2026-11-13 00:00Z** contains **42 ordinary PRs**, excluding Mergify's temporary
  queue PRs. Their final heads have five successful Zuul checks, four cancelled checks, one failure and
  **32 missing reports**. Thirty of those PRs merged before the first retained Zuul event; [#1450](https://github.com/kristofk/AIDepartment/pull/1450)'s head has
  no retained Zuul check either. The later missing report on [#1470](https://github.com/kristofk/AIDepartment/pull/1470)'s final head is covered by [#1519](https://github.com/kristofk/AIDepartment/issues/1519) below.
  A success on an earlier head is never substituted for a missing final-head report.

### Results and reporting time, per exact head

For a head with several attempts, pair the latest Zuul check buildset with the newest completed Actions cheap
gate by check-run ID. Keep the other attempts in the snapshot. The Actions workflow record's `head_sha` was checked against the
Zuul patchset for every pair.

Times below are **minutes:seconds**, including waits. Zuul to report is its trigger `event_timestamp` to the
GitHub check's `completed_at`. Actions to report is the CI workflow's `created_at` to `Cheap checks passed`'s
`completed_at`. Completion delta is Zuul's completion minus Actions' completion: positive means Zuul reported
later on the wall clock. These are distinct starts: draft/ready behavior and re-enqueues can shift them by an hour.
The delta is not a speedup or regression measurement. All heads in this table were observed on November 14.

| PR / exact head | Zuul / Actions | Zuul to report | Actions to report | Completion delta |
|---|---|---:|---:|---:|
| [#1470](https://github.com/kristofk/AIDepartment/pull/1470) / `32077d29` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132499548020) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43605549342) | 1:15 | 13:53 | 62:04 |
| [#1470](https://github.com/kristofk/AIDepartment/pull/1470) / `a3ca4969` | [FAILURE](https://github.com/kristofk/AIDepartment/runs/132628082522) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43654403508) | 14:29 | 7:20 | 0:08 |
| [#1474](https://github.com/kristofk/AIDepartment/pull/1474) / `1f0afba5` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132549014136) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43628247252) | 12:14 | 9:23 | 2:51 |
| [#1474](https://github.com/kristofk/AIDepartment/pull/1474) / `ba96619c` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132499610686) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43605905159) | 1:48 | 14:08 | 58:24 |
| [#1474](https://github.com/kristofk/AIDepartment/pull/1474) / `c7e2595b` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132610404780) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43648049421) | 12:54 | 9:38 | 3:18 |
| [#1474](https://github.com/kristofk/AIDepartment/pull/1474) / `cf895512` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132519742693) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43618815338) | 19:40 | 10:27 | 9:12 |
| [#1478](https://github.com/kristofk/AIDepartment/pull/1478) / `d5227eeb` | [FAILURE](https://github.com/kristofk/AIDepartment/runs/132499677949) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43607863170) | 10:52 | 9:07 | 50:04 |
| [#1489](https://github.com/kristofk/AIDepartment/pull/1489) / `e5909615` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132523910316) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43621079386) | 23:35 | 4:13 | 8:50 |
| [#1496](https://github.com/kristofk/AIDepartment/pull/1496) / `d5411f1b` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132512189866) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43616815514) | 1:42 | 3:36 | −7:52 |
| [#1500](https://github.com/kristofk/AIDepartment/pull/1500) / `0e10922f` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132543518165) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43626782509) | 5:59 | 3:31 | −0:39 |
| [#1503](https://github.com/kristofk/AIDepartment/pull/1503) / `2e325e24` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132584151235) / [failure](https://github.com/kristofk/AIDepartment/actions/runs/43640042240) | 11:06 | 4:37 | 1:23 |
| [#1503](https://github.com/kristofk/AIDepartment/pull/1503) / `4a7f080e` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132588613848) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43641752027) | 7:11 | 8:25 | −9:12 |
| [#1503](https://github.com/kristofk/AIDepartment/pull/1503) / `db5ccdef` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132598837233) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43646800632) | 24:54 | 9:54 | −12:55 |
| [#1507](https://github.com/kristofk/AIDepartment/pull/1507) / `1bfcfe44` | [FAILURE](https://github.com/kristofk/AIDepartment/runs/132595352443) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43643259456) | 16:19 | 5:21 | 10:58 |
| [#1509](https://github.com/kristofk/AIDepartment/pull/1509) / `6348024a` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132608008867) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43647318348) | 11:51 | 4:22 | 7:28 |
| [#1509](https://github.com/kristofk/AIDepartment/pull/1509) / `964a2820` | [SUCCESS](https://github.com/kristofk/AIDepartment/runs/132598172692) / [success](https://github.com/kristofk/AIDepartment/actions/runs/43644144137) | 19:02 | 5:11 | 13:52 |

Across these 16 pairs the median trigger-to-report is **12:02.5 for Zuul**, **7:52.5 for Actions**; the median
wall-clock completion delta is **+5:23**. Zuul's range is 1:15–24:54, Actions' 3:31–14:08, and the completion
delta −12:55 to +62:04. These describe this mixed rollout/draft/ready sample and its unequal job coverage;
they do not predict a full Zuul gate's latency.

### Each difference and its disposition

| Difference | Evidence and disposition |
|---|---|
| Zuul green, Actions red: missing cheap jobs | [#1503](https://github.com/kristofk/AIDepartment/pull/1503) at `2e325e24`: Actions' [plan job](https://github.com/kristofk/AIDepartment/actions/runs/43640042240/job/132585383940) failed its workflow-policy tests; all four Zuul lint jobs passed. The PR's policy defect was fixed by `4a7f080e`, but Zuul still never ran it. **[#1520](https://github.com/kristofk/AIDepartment/issues/1520)** tracks the absent quarantine validation, CI/policy tests, capture check, affected plan and Node suites. |
| Zuul red, Actions green: node environment | [#1478](https://github.com/kristofk/AIDepartment/pull/1478) at `d5227eeb`: Zuul's apps lint jobs failed. **[#1495](https://github.com/kristofk/AIDepartment/issues/1495)**, fixed by [#1496](https://github.com/kristofk/AIDepartment/pull/1496), records `node: command not found`, the ignored SSH `SetEnv` lines and the correction. On [#1496](https://github.com/kristofk/AIDepartment/pull/1496) at `d5411f1b` both systems pass. |
| Zuul red, Actions green: self-test timeout | [#1470](https://github.com/kristofk/AIDepartment/pull/1470) at `a3ca4969` and [#1507](https://github.com/kristofk/AIDepartment/pull/1507) at `1bfcfe44`: `aid-apps-lint-test` is `TIMED_OUT` in 617 and 719 seconds. The same self-test passes Actions in four and five seconds respectively; the other three Zuul jobs passed. **[#1513](https://github.com/kristofk/AIDepartment/issues/1513)** owns the investigation; this comparison adds the second occurrence there. The cause is unconfirmed; no retry masks it. |
| No new check: broken live reporter rollout | At the captured status the tenant has **only `hold`**. `check` and `check-external` reject `start.aid-events`, gate rejects `enqueue.aid-events`, and hold then supercedes an unknown gate. The running scheduler's connections are only `github`, `aid-nodes`, `opendev.org`. Trusted config `1731ebb3` introduced the MQTT reporters at 00:22:16Z, after the last retained trigger. [#1470](https://github.com/kristofk/AIDepartment/pull/1470)'s later merged head `ceb0cb24` has no `aid/check`. **[#1519](https://github.com/kristofk/AIDepartment/issues/1519)** records the missing connection/rollout prerequisites and restoration criteria. |
| No full-gate parity | **Zero gate buildsets**, and its sole configured job is `aid-bazel-everything`. **[#1521](https://github.com/kristofk/AIDepartment/issues/1521)** tracks queue-layer validation missing beyond Bazel: architecture, ingress, images, infrastructure checks and release staging. **[#1485](https://github.com/kristofk/AIDepartment/issues/1485)** already owns the actual node's Swift toolchain. **[#1350](https://github.com/kristofk/AIDepartment/issues/1350)** separately owns post/periodic/release publishing. |
| No retained job logs for diagnosis | The two timeout buildsets have null job log URLs and error details. **[#1352](https://github.com/kristofk/AIDepartment/issues/1352)** owns retained logs/UI/health; do not claim an assertion or root cause from a timeout alone. |

Other unsuccessful records are not unexplained cross-system parity differences: the fork and node-acceptance
probes are excluded above; both systems' cancellations/skips are not successes. Ordinary merge conflicts are
recorded for [#1478](https://github.com/kristofk/AIDepartment/pull/1478) `bb5afb31` and [#1500](https://github.com/kristofk/AIDepartment/pull/1500) `fc80a518`, with no completed Actions cheap gate to pair. [#1503](https://github.com/kristofk/AIDepartment/pull/1503)'s draft
`c0318e8b` fails the apps lint jobs before its documentation references were corrected in `9da0b5ca`, and has no
completed Actions cheap gate. The hold buildsets run only `noop`, so their results say nothing about CI coverage.
These records remain available in the snapshot without being promoted to matched or mismatched full CI results.

The intended trigger differences already documented in [gate.md](gate.md) also remain: Zuul checks collaborators'
drafts, Actions waits for ready; external heads follow the trusted guard rather than receiving the farm token;
gate requires an approval of the current head and rejects `hold`. No new exception to those rules is inferred
from this comparison. [#1519](https://github.com/kristofk/AIDepartment/issues/1519) explains the observed missing late pipeline report; it is not counted as a pass.

### Approval to merge

Only four cohort PRs have a formal approving review by the other bot or Kristóf on the **exact final head**.
The earlier loop used comment reviews; those 38 PRs have no comparable formal approval timestamp. They remain
in the snapshot with a null approval time. Do not use an approval of an older SHA, PR creation, or the start of
a merge-queue draft as a substitute. The measured interval includes subsequent ready/queue waits and builds.

| PR / merged head | Approval of that head (UTC) | Merged (UTC) | Approval to Mergify merge |
|---|---|---|---:|
| [#1500](https://github.com/kristofk/AIDepartment/pull/1500) / `0e10922f` | [2026-11-14T18:28:04Z](https://github.com/kristofk/AIDepartment/pull/1500#pullrequestreview-5906353025) | 2026-11-14T18:34:27Z | 6:23 |
| [#1503](https://github.com/kristofk/AIDepartment/pull/1503) / `db5ccdef` | [2026-11-14T22:11:09Z](https://github.com/kristofk/AIDepartment/pull/1503#pullrequestreview-5907399106) | 2026-11-15T00:19:19Z | 128:10 |
| [#1509](https://github.com/kristofk/AIDepartment/pull/1509) / `6348024a` | [2026-11-14T22:45:32Z](https://github.com/kristofk/AIDepartment/pull/1509#pullrequestreview-5907574492) | 2026-11-15T00:33:45Z | 108:13 |
| [#1470](https://github.com/kristofk/AIDepartment/pull/1470) / `ceb0cb24` | [2026-11-15T00:35:41Z](https://github.com/kristofk/AIDepartment/pull/1470#pullrequestreview-5908174475) | 2026-11-15T01:36:56Z | 61:15 |

These are observed **Mergify** merge times. Every row's hypothetical **Zuul** approval-to-merge interval is
**unmeasured**: there are no production gate buildsets, and check's lint-only completion cannot predict it.

### Gate mode and the cutover

The trusted gate is **merge-enabled** (`success.github.merge: true`), not a reporting-only gate. While it was
loaded, branch protection's required `Every package passed` prevented ordinary heads from entering its dependent
queue ([gate.md](gate.md)). At capture the MQTT connection error prevents even loading it. Consequently [#1353](https://github.com/kristofk/AIDepartment/issues/1353)'s
original reporting-only shadow-gate exercise and week without unexplained differences have **not occurred**.
The owner's instruction lets the comparison report the existing data and move on; it does not turn those missing
observations into a successful exercise.

Do not remove today's protection, merge through Zuul experimentally, dispatch comparison builds, or hold work
for a week to fill these cells. [#1355](https://github.com/kristofk/AIDepartment/issues/1355) must account for the coverage and rollout issues above before retiring
Actions/Mergify. A working full gate and its required jobs are implementation prerequisites, not a reason to
extend the measurement period. No recommendation to cut over can be derived from these 16 cheap-check pairs.

### Reproducing the reads

Read-only GitHub queries, with `gh` authenticated as the builder:

```sh
gh pr list --repo kristofk/AIDepartment --state merged --search 'merged:>=2026-11-13' \
  --limit 100 --json number,author,title,mergedAt
gh pr view <number> --repo kristofk/AIDepartment --json headRefOid,reviews,mergedAt,url,author
gh api --paginate --slurp 'repos/kristofk/AIDepartment/commits/<sha>/check-runs?per_page=100&filter=all'
gh api --paginate --slurp 'repos/kristofk/AIDepartment/pulls/<number>/reviews?per_page=100'
gh api repos/kristofk/AIDepartment/actions/runs/<run-id>
gh api repos/kristofk/AIDepartment/actions/jobs/<job-id>
gh api repos/kristofk/aid-zuul-config/commits/main
```

The cohort listing returned fewer than 100 PRs, including queue drafts; filter `author.is_bot` for the ordinary
cohort. For a later larger cohort, increase the limit or paginate. Read check runs for **every recorded Zuul head**,
not just `gh pr view`'s current rollup, which loses earlier-head discrepancies. Follow the cheap gate's Actions URL
to its run ID; verify the workflow's `head_sha`. Do not pair a Mergify queue draft's synthetic head with an original
PR head. Keep the GitHub source IDs/URLs in the snapshot and preserve all attempts before selecting a pair.

The box cannot resolve the cluster-only web address directly. Read Zuul's API inside its existing web pod with
the tenant's already granted kubeconfig; no port-forward, new pod or service mutation is needed:

```sh
kubectl --kubeconfig ~/.kube/devinfra.yaml -n zuul exec deploy/web -- python3 -c \
  'import urllib.request; print(urllib.request.urlopen("http://localhost:9000/api/tenant/aid/buildsets?limit=100&skip=0").read().decode())'
```

Read the same way `buildset/<uuid>` for each retained AID buildset, `status`, and `config-errors`. Paginate
`buildsets` by `skip` until a short page; the captured first page contained 82, so was complete for retained history.
Zuul's SQL timestamps here are UTC without an offset; the snapshot normalizes them with `Z`. Record a separate
capture time for history and live status. Inspect only the names of the scheduler's connection sections, never
publish `zuul.conf`'s credential values. No credential, console body, private hostname or raw webhook payload is
included in this snapshot.
