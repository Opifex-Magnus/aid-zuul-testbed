#!/usr/bin/env bash
# cleanup post-run entry point; trusted post jobs pass their durable CI_SCRATCH_STATE/endpoints.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
build=${1:?Zuul build UUID required}
[[ $build =~ ^[0-9a-f]{32}$ ]] || { echo 'scratch-cleanup: invalid build UUID' >&2; exit 2; }
export GITHUB_RUN_ID=$build GITHUB_RUN_ATTEMPT=1 RUNNER_NAME=zuul-$build
export CI_SCRATCH_STATE=${CI_SCRATCH_STATE:-$HOME/zuul-scratch/$build}
endpoints=${GATE_ENDPOINTS:-/etc/aid-gate/endpoints.env}
if [ -r "$endpoints" ]; then
  for key in CI_SCRATCH_REGISTRY CI_RUN_NAMESPACE CI_SCRATCH_LIFECYCLE; do
    value=$(sed -n "s/^$key=//p" "$endpoints" | tail -n 1)
    [ -z "$value" ] || export "$key=$value"
  done
fi
export KUBERNETES_SERVICE_HOST=${KUBERNETES_SERVICE_HOST:-kubernetes.default.svc} KUBERNETES_SERVICE_PORT=${KUBERNETES_SERVICE_PORT:-443}
# Always try pod cleanup; the trusted fence below must confirm consumers have actually stopped.
status=0
node ci/image-run.mjs cleanup || status=$?
if [ "${CI_SCRATCH_LIFECYCLE:-}" = 1 ]; then
  # Fail closed: no manifest deletion if fencing or receipt generation fails. Never ignore this error.
  /usr/local/libexec/aid-scratch-control fence
  node ci/scratch-lifecycle.mjs cleanup
fi
exit "$status"
