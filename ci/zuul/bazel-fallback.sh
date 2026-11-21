#!/usr/bin/env bash
# Isolated real-Bazel loopback lab: no shared farm calls, credentials, caches or output directories.
# Zuul bounds the whole job; timeout bounds the simulator too, including its per-case watchdogs.
set -euo pipefail
[ $# -eq 1 ] || { echo 'usage: ci/zuul/bazel-fallback.sh <timeout-seconds>' >&2; exit 2; }
[[ $1 =~ ^[1-9][0-9]*$ ]] || exit 2
cd "$(git rev-parse --show-toplevel)"
# The node image supplies pinned Node and Bazelisk. Fail before the lab if the runtime differs.
expected_node=$(awk '$1 == "node" {print $3}' ci/tools.pin)
[ "$(node --version)" = "v$expected_node" ]
export USE_BAZEL_VERSION=$(cat .bazelversion)
export BAZEL=$(command -v bazel)
[ "$("$BAZEL" --version)" = "bazel $USE_BAZEL_VERSION" ]
# A private copy prevents competing Bazel servers or modifications to the job's checkout.
lab=$(mktemp -d)
trap 'rm -rf "$lab"' EXIT
mkdir "$lab/checkout"
git ls-files -z | tar --null -T - -cf - | tar -xf - -C "$lab/checkout"
cd "$lab/checkout"
timeout --signal=TERM --kill-after=20s "$1" node ci/bazel-fallback.mjs
