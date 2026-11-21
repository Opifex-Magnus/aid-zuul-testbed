# Gate compute home investigation (#1867 / #1926)

## What ran

Measured on the builder box against the farm, at routing head `923d60a0`
(main base `d522f004`), with Bazel 9.3.2 and the full job's `test //...`,
`--host_jvm_args=-Xmx3g`, `--config=ci --config=zuul`, explicit minimal downloads,
and raw RPC/execution/BEP diagnostics. `ci/bazel-job.py` wraps the invocation.
Only farm connectivity/auth is adapted to the box. Each comparison has isolated
home/cache/output/install paths and an explicitly selected copy of the node rc;
`--batch` keeps client lifetime bounded on the box. No compilation/test action
runs on the box; Bazel's repository fetching and metadata work are local as on a
Zuul node.

The first run uses a cold cache. Later comparisons prefill only the immutable
compressed-download entries from that run, with separate directory trees and
fresh extracted contents/output bases. Those compressed entries remain inside
each measured home and are counted; they are not excluded as an off-home cache.
The final cold implementation run uses rebased main `f77564a7` plus this fix.
It adds a private checkout copy (including its own Git object pack) inside the
measured home; the comparison baselines exclude their checkout.

Sizes are allocated bytes from one non-following `du` traversal per sample, which
counts hardlinks once. Ten-second sampling establishes sampled peaks, not exact
instantaneous maxima. The final cold fixed run samples every second (plus traversal time) throughout.
A final post-exit traversal is included below to account for log finalization
(8834.6 MiB sampled while running; 8839.7 MiB after finalization).

## Both image configurations

The PR/check image (`zuul-node`) and gate image (`zuul-post-node`, mapped to the
`zuul-gate-node` PodTemplate name) inherit the same `zuul_node_files` layer,
`/etc/bazel.bazelrc`, `/usr/local/bin/bazel` and start-node. The post image adds
packages and `/opt/zuul-node-check/swift`, not another rc or home cache policy.
The source rc SHA-256 is
`09741315dd9f21304dbab4fd9f82746e22c516ebdfa4c1393d8808cd68bf0ff3`;
the source rc diff is empty.

- Startup: repository rc's 25% JVM default, overridden by job `-Xmx3g`.
- Default output user root: `/home/zuul/.cache/bazel/_bazel_zuul`.
- Default output base: the workspace hash under that root. No job override.
- Download mode: **minimal**, explicitly in the full job already.
- Repository cache: `/home/zuul/.cache/bazel/repository-cache`.
- Expanded repo contents cache: default `<repository_cache>/contents`.
- Action disk cache: unset (empty); no such directory measured.
- Read-only shared downloaded blobs seed the writable private cache via start-node;
  no action-result cache or trusted write access is added.

Successful gate-image build `22b34d87deed4fbb801416a02532b2f5` independently
confirms the output/cache paths, `-Xmx3g`, remote spawn strategy and minimal mode
in its canonical structured BEP command line:
https://aid.taila5826.ts.net:5788/t/aid/build/22b34d87deed4fbb801416a02532b2f5

## Sampled peaks (MiB)

| Configuration | Whole home | Output base | Repository cache | Install base |
| --- | ---: | ---: | ---: | ---: |
| node rc | 13662.3 | 4414.0 | 9162.4 | 79.7 |
| gate rc (same source rc) | 13659.2 | 4414.3 | 9162.4 | 79.7 |
| hardlinks alone | 13712.6 | 4464.8 | 9162.6 | 79.7 |
| hardlinks + no expanded contents cache | 13763.6 | 9127.4 | 4551.1 | 79.7 |
| repository cache disabled, JSON log (before finalization) | 9134.2 | 9049.0 | 0 | 79.7 |
| repository cache disabled, compact log, cold final code | 8839.7 | 8685.1 | 0 | 79.7 |

Component peaks occur at different times, so do not sum them as a measured total.
Default repository cache splits into about 4551 MiB compressed blobs and 4611 MiB
expanded repository contents. Output-base growth includes a roughly 2.7 GiB
Swift toolchain and the static SDK, while the cached inputs include a 1069.7 MiB
Swift image payload and roughly 1.1 GiB Chromium repository. Diagnostics are only
a few MiB at the action-work boundary; they are not the multi-GiB contributor.
The hardlink option did not deduplicate the dominant downloaded payloads (distinct
inodes persisted), so neither hardlink variant is a proposed fix.

## Memory limit on this reproduction

These baseline commands finish their action work but exit **137**, with the box's
cgroup OOM-kill counter increasing. The box is limited to 4 GiB, whereas the real
compute pod has 6 GiB. They are storage measurements, **not passing full-test
runs**. The reported sampled peak already exceeds the 11 GiB home ceiling by over
2 GiB before final diagnostics/teardown. Production heap/memory are unchanged.

## Proposed fix and final validation

Only the full job overrides `--repository_cache=`. Bazel 9.3.2 documents that an
empty repository cache also disables the default repo-contents cache unless an
explicit alternative is set (neither image sets one). No duplicate blob archive
is retained alongside the materialized repositories. The tradeoff is fresh
repository HTTP fetching for this disposable full-job invocation; other jobs
keep their private seeded cache. Hardlinks alone were measured and rejected.

The job uses `--execution_log_compact_file` instead of JSON logging. Bazel's help
recommends this zstd-compressed ExecLogEntry format as smaller and cheaper to
produce; unlike JSON it needs no final sorting. The successful gate-image
manifest records **2246351480 bytes** for the old JSON execution log; that
expensive finalization is absent from the OOM-truncated baseline measurements.

The final cold run kept the production **3 GiB heap**, remote-only execution,
minimal outputs and all raw diagnostics, with no repository cache. It completed
with **exit 0**, `Executed 104 out of 170 tests: 170 tests pass.` Bazel reported
413.092 seconds, 242 remote processes, 3048 remote cache hits; no local fallback.
The measured home includes a 64 MiB private checkout copy. The compact raw log
is **4250979 bytes**, versus the successful gate-image JSON's 2246351480 bytes.
The same bounded exporter recorded it as omitted because it slightly exceeds
the unchanged 4 MiB per-file limit; RPC/BEP and bounded test reports were fetched.
The compact log is never published as a truncated binary or JSON preview.

Reproduction invocation and size samples are retained at
`/tmp/1867-storage-evidence/no-cache-compact-final/{command.json,sizes.jsonl,run.log}`
on the builder. These raw `/tmp` paths are ephemeral and are not a retention
guarantee. The exact public invocation, final allocation traversal and aggregate
results are also archived permanently in [issue #1867](https://github.com/kristofk/AIDepartment/issues/1867#issuecomment-7041163641).
`command.json` contains public flags/paths, never helper output.
The farm endpoint/helper are box-specific; all job flags come from
`ci/zuul/zuul.d/jobs.yaml`. Startup adds `--batch --nosystem_rc --nohome_rc` and
selects the copied node rc explicitly. Output/install/home/log paths are
relocated to isolate and measure this run.

This is a box storage reproduction, not another production activation gate.
Actual compute activation and host pressure remain the orchestrator's next
explicitly released gate; #1926 stays draft with hold until then. No image,
heap, pod resources, namespace quota or other tenant is changed.

This prerequisite lands only the footprint settings and bounded exporter; its gate
uses the unchanged gate-image nodeset. Compute routing remains a separate held
change in #1926. No PodTemplate apply is needed for these job settings.
