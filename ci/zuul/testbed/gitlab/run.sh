#!/usr/bin/env bash
# run.sh: the GitLab test bed (../../forge.md, "The check on GitLab"). Starts GitLab CE and Zuul in Docker Compose,
# puts the two copies into GitLab, and runs scenario.py: merge requests through check and gate, as the two-agent loop's
# would. It runs in the GitLab test bed's workflow (workflow.yml), in a copy that sync.sh made, on a machine with
# Docker and passwordless sudo, and needs no secret and no account anywhere:
#   ci/zuul/testbed/gitlab/run.sh
# Needs docker (with compose), git, jq, openssl, ssh-keygen and python3. Writes the scenario's results to
# $TESTBED_RESULTS (default ./gitlab-results.json).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../../.." && pwd)
env_file="$root/ci/zuul/testbed/testbed.env"
[ -f "$env_file" ] && [ -d "$root/testbed-config" ] || { echo "run.sh runs in a copy made by gitlab/sync.sh" >&2; exit 2; }
results=${TESTBED_RESULTS:-$PWD/gitlab-results.json}

work=$(mktemp -d)
export TESTBED_WORK=$work
compose=(docker compose --project-directory "$here" -f "$here/compose.yaml" --env-file "$env_file")
logs() {
  echo "== the services' logs"
  "${compose[@]}" logs --no-color --timestamps scheduler web executor launcher || true
  "${compose[@]}" logs --no-color --timestamps --tail 200 gitlab || true
  echo "== the failed builds' output"
  # shellcheck disable=SC2016 # expanded in the executor's shell
  "${compose[@]}" exec -T executor sh -c 'ls /var/lib/zuul/builds
    for output in /var/lib/zuul/builds/*/work/logs/job-output.txt; do
      grep -qE "FAILED|fatal:|failed=[1-9]|unreachable=[1-9]|ERROR" "$output" || continue
      echo "--- $output"; grep -nE -B2 -A12 "FAILED|fatal:|ERROR" "$output" | tail -n 80
    done' 2>&1 || true
}
finish() {
  local status=$?
  [ "$status" -eq 0 ] || logs
  [ -z "${reports_pid:-}" ] || kill "$reports_pid" 2>/dev/null || true
  "${compose[@]}" down --volumes --timeout 10 > /dev/null 2>&1 || true
  sudo rm -rf "$work"
  exit "$status"
}
trap finish EXIT

password() { openssl rand -hex 24; }
ZUUL_KEYSTORE_PASSWORD=$(password)
ZUUL_DB_PASSWORD=$(password)
DB_ROOT_PASSWORD=$(password)
ZUUL_WEBHOOK_TOKEN=$(password)
GITLAB_ROOT_PASSWORD=$(password)
GITLAB_TOKEN=glpat-$(openssl rand -hex 16)
ZUUL_ADMIN_SECRET=$(openssl rand -hex 32)
export ZUUL_KEYSTORE_PASSWORD ZUUL_DB_PASSWORD DB_ROOT_PASSWORD ZUUL_WEBHOOK_TOKEN GITLAB_ROOT_PASSWORD GITLAB_TOKEN \
  ZUUL_ADMIN_SECRET

echo "== keys"
"$root/deploy/zuul/secrets.sh" ca "$work/ca"
printf '%s' "$ZUUL_KEYSTORE_PASSWORD" > "$work/keystore"
"$root/deploy/zuul/secrets.sh" zuul --ca-cert "$work/ca/ca.crt" --ca-key "$work/ca/ca.key" \
  --keystore-password "$work/keystore" > "$work/secrets.json"
unpack() { # unpack <Secret> <key> <file>
  jq -er --arg name "$1" --arg key "$2" '.items[] | select(.metadata.name == $name) | .data[$key] | @base64d' \
    "$work/secrets.json" > "$3"
}
mkdir -p "$work/zookeeper-tls" "$work/client-tls" "$work/etc" "$work/ssh" "$work/logs"
unpack zookeeper-tls keystore.pem "$work/zookeeper-tls/keystore.pem"
unpack zookeeper-tls ca.crt "$work/zookeeper-tls/ca.crt"
for key in tls.crt tls.key ca.crt; do unpack zuul-zookeeper-client "$key" "$work/client-tls/$key"; done
chmod -R a+rX "$work/zookeeper-tls" "$work/client-tls"
# The log store the base job's post-run writes into (deploy/zuul/executor/store-logs.sh), from inside the executor's
# bubblewrap, whose user is not the runner's: writable by anyone, as only the run's builds write there (#1540).
chmod 1777 "$work/logs"
ssh-keygen -q -t ed25519 -N '' -C zuul-gitlab-testbed -f "$work/ssh/id_rsa"
chmod 644 "$work/ssh/id_rsa.pub"

echo "== configuration"
# The deployment's zuul.conf without its GitHub App, its MQTT broker and its statsd (as ../run.sh), plus GitLab's.
awk '/^\[(connection "(github|aid-events)"|statsd)\]$/ { skip = 1; next } /^\[/ { skip = 0 } !skip' \
  "$root/deploy/zuul/server/config/zuul.conf" > "$work/etc/deployment.conf"
cat "$work/etc/deployment.conf" "$here/zuul.conf" > "$work/etc/zuul.conf"
rm "$work/etc/deployment.conf"
cp "$here/main.yaml" "$work/etc/main.yaml"
chmod -R a+rX "$work/etc"

echo "== the copies, for GitLab"
# What GitLab gets: AID's copy without the config copy and the workflow, and the config copy, each changed only where
# GitLab needs it (../../forge.md, "What changed for GitLab").
stage=$work/stage
mkdir -p "$stage/aid" "$stage/config"
tar -C "$root" --exclude=./.git --exclude=./testbed-config --exclude=./.github -c . | tar -C "$stage/aid" -x
tar -C "$root/testbed-config" --exclude=./.git -c . | tar -C "$stage/config" -x
# 1. The pipelines: aid-zuul-config's own (the original that ../sync.sh saved), with GitLab's triggers and reporters.
scheduler=$(sed -n 's/^ZUUL_SCHEDULER_IMAGE=//p' "$env_file")
docker run --rm --quiet --entrypoint python3 \
  --mount "type=bind,src=$here,dst=/gitlab,readonly" \
  --mount "type=bind,src=$root/ci/zuul/testbed/original-pipelines.yaml,dst=/original.yaml,readonly" \
  --mount "type=bind,src=$stage/config/zuul.d,dst=/out" --user "$(id -u)" \
  "$scheduler" -I /gitlab/translate.py /original.yaml /gitlab/pipelines.yaml /out/pipelines.yaml
# 2. The merge mode: GitLab's reporter merges with merge, merge-resolve or squash-merge, and refuses rebase.
project=$stage/aid/ci/zuul/zuul.d/project.yaml
[ "$(grep -c '^    merge-mode: rebase$' "$project")" -eq 1 ] || { echo "$project: not one merge-mode: rebase" >&2; exit 1; }
sed -i 's/^    merge-mode: rebase$/    merge-mode: merge/' "$project"

echo "== GitLab"
"${compose[@]}" up --detach --quiet-pull --wait --wait-timeout 1200 gitlab zookeeper db events
# root's token for setup and the scenario, with sudo, so the scenario acts as builder and reviewer (GitLab's documented
# way to make one without the web UI).
"${compose[@]}" exec -T -e GITLAB_TOKEN gitlab gitlab-rails runner '
  token = User.find_by_username("root").personal_access_tokens.create!(
    name: "testbed", scopes: [:api, :sudo, :write_repository], expires_at: 2.days.from_now)
  token.set_token(ENV.fetch("GITLAB_TOKEN"))
  token.save!'
GITLAB_HOOK_URL=http://web:9000/api/connection/gitlab/payload \
  python3 -I -B "$here/scenario.py" setup --aid "$stage/aid" --config "$stage/config" \
  --zuul-token-file "$work/zuul-token"
ZUUL_GITLAB_TOKEN=$(cat "$work/zuul-token")
export ZUUL_GITLAB_TOKEN

echo "== Zuul"
"${compose[@]}" build --quiet node
"${compose[@]}" exec -T events mosquitto_sub -t aid/zuul/reports -q 1 -i gitlab-testbed > "$work/reports.jsonl" &
reports_pid=$!
"${compose[@]}" up --detach --quiet-pull
"${compose[@]}" ps

echo "== scenario"
python3 -I -B "$here/scenario.py" run --zuul http://127.0.0.1:9000 --results "$results"

echo "== AID's events from GitLab"
# What a Zuul report says about a GitLab change, for AID's zuul app (../../forge.md, "AID's events").
jq -c 'select(.pipeline == "gate") | {action, pipeline, project, change, patchset, ref, change_url, owner}' \
  "$work/reports.jsonl" | head -n 5
jq -s --slurpfile results "$results" '$results[0] + {reports: length, gate_report: (map(select(.pipeline == "gate"))[0]
  | {project, change, patchset, ref, change_url, owner})}' "$work/reports.jsonl" > "$results.tmp" && mv "$results.tmp" "$results"
cat "$results"
