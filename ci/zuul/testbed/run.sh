#!/usr/bin/env bash
# run.sh: the test bed (../README.md, "The test bed"). Starts Zuul in Docker Compose with the layout of ci/zuul/, then
# runs scenario.py against it, and prints the services' logs if anything fails. It runs in the test bed's workflow
# (workflow.yml), in a copy that sync.sh made, on a machine with Docker and passwordless sudo:
#   GITHUB_TOKEN=... TESTBED_REPOSITORY=owner/name ci/zuul/testbed/run.sh
# GITHUB_TOKEN must be able to push branches, open and close pull requests and set commit statuses in the test bed's
# repository. The config project's copy, which testbed.env names, must be public: Zuul reads it with that token.
# Needs docker (with compose), jq, openssl, ssh-keygen and python3.
set -euo pipefail

: "${GITHUB_TOKEN:?run.sh needs GITHUB_TOKEN}"
: "${TESTBED_REPOSITORY:?run.sh needs TESTBED_REPOSITORY, owner/name}"
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
[ -f "$here/testbed.env" ] || { echo "no testbed.env: run.sh runs in a copy made by sync.sh" >&2; exit 2; }

work=$(mktemp -d)
export TESTBED_WORK=$work
compose=(docker compose --project-directory "$here" -f "$here/compose.yaml" --env-file "$here/testbed.env")
logs() {
  echo "== the services' logs"
  "${compose[@]}" logs --no-color --timestamps || true
  # The end of each failed build's output (compose.yaml keeps the executor's build folders).
  echo "== the failed builds' output"
  # shellcheck disable=SC2016 # expanded in the executor's shell
  "${compose[@]}" exec -T executor sh -c 'ls /var/lib/zuul/builds
    for output in /var/lib/zuul/builds/*/work/logs/job-output.txt; do
      grep -qE "FAILED|fatal:|failed=[1-9]|unreachable=[1-9]" "$output" || continue
      echo "--- $output"; tail -n 60 "$output"
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

# The run's secrets, made here and gone with it.
password() { openssl rand -hex 24; }
ZUUL_KEYSTORE_PASSWORD=$(password)
ZUUL_DB_PASSWORD=$(password)
DB_ROOT_PASSWORD=$(password)
ZUUL_WEBHOOK_TOKEN=$(password)
ZUUL_GITHUB_TOKEN=$GITHUB_TOKEN
ZUUL_ADMIN_SECRET=$(openssl rand -hex 32)
export ZUUL_KEYSTORE_PASSWORD ZUUL_DB_PASSWORD DB_ROOT_PASSWORD ZUUL_WEBHOOK_TOKEN ZUUL_GITHUB_TOKEN ZUUL_ADMIN_SECRET

echo "== keys"
# ZooKeeper's CA and key pairs, as the deployment makes them (deploy/zuul/secrets.sh).
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
# The services run as their images' users, ZooKeeper's 1000 and Zuul's 10001 (the executor as root); these keys are
# the run's own.
chmod -R a+rX "$work/zookeeper-tls" "$work/client-tls"
ssh-keygen -q -t ed25519 -N '' -C zuul-testbed -f "$work/ssh/id_rsa"
chmod 644 "$work/ssh/id_rsa.pub"

echo "== configuration"
# The deployment's zuul.conf, plus the test bed's connections; the deployment's tenant file is replaced by the test
# bed's, with the two repositories put in.
config=$(sed -n 's/^TESTBED_CONFIG_REPOSITORY=//p' "$here/testbed.env")
for repository in "$TESTBED_REPOSITORY" "$config"; do
  [[ "$repository" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "'$repository' is not owner/name" >&2; exit 2; }
done
# The deployment's own connection github is ThunderBox's App (deploy/zuul/github.md), which the test bed does not have:
# it is left out, and the test bed's, with the run's token, takes its place. So is its statsd: the exporter it names,
# metrics.zuul.svc.cluster.local, is not here, and Zuul's statsd client stops a service whose host does not resolve.
awk '/^\[(connection "(github|aid-events)"|statsd)\]$/ { skip = 1; next } /^\[/ { skip = 0 } !skip' "$root/deploy/zuul/server/config/zuul.conf" \
  > "$work/etc/deployment.conf"
[ "$(grep -c '^\[connection "github"\]$\|^\[connection "aid-events"\]$\|^\[statsd\]$' "$work/etc/deployment.conf")" -eq 0 ] \
  || { echo "the deployment's connection github or aid-events, or its statsd, is still there" >&2; exit 1; }
cat "$work/etc/deployment.conf" "$here/zuul.conf" > "$work/etc/zuul.conf"
rm "$work/etc/deployment.conf"
sed -e "s#@REPOSITORY@#$TESTBED_REPOSITORY#g" -e "s#@CONFIG_REPOSITORY@#$config#g" "$here/main.yaml" > "$work/etc/main.yaml"
chmod -R a+rX "$work/etc"

echo "== patch"
# The test bed's token cannot read branch protection, which Zuul asks for on every pull request: patch.py says why,
# and changes copies of the two files of Zuul's GitHub driver that do, which compose.yaml mounts into each service.
scheduler=$(sed -n 's/^ZUUL_SCHEDULER_IMAGE=//p' "$here/testbed.env")
echo "== original GitHub pipelines and gate's write-approval requirement"
docker run --rm --quiet -i --entrypoint python3 \
  --mount "type=bind,src=$here/original-pipelines.yaml,dst=/original-pipelines.yaml,readonly" \
  "$scheduler" - /original-pipelines.yaml < "$here/check-github-gate.py"
echo "== MQTT publish does not wait for AID or broker acknowledgment"
docker run --rm --quiet -i --entrypoint python3 "$scheduler" - < "$here/check-mqtt.py"
graphql=$(docker run --rm --quiet --entrypoint python3 "$scheduler" -c \
  'import os, zuul.driver.github.graphql as g; print(os.path.dirname(g.__file__))')
mkdir -p "$work/graphql-original"
docker run --rm --quiet --entrypoint tar "$scheduler" -C "$graphql" -c __init__.py canmerge.graphql \
  | tar -x -C "$work/graphql-original"
python3 -I -B "$here/patch.py" "$work/graphql-original" "$work/graphql"
chmod -R a+rX "$work/graphql"
export ZUUL_GRAPHQL=$graphql

echo "== start"
"${compose[@]}" build --quiet node
secrets=$(sed -n 's/^TESTBED_SECRETS=//p' "$here/testbed.env")
if [ "$secrets" = 1 ]; then
  # The test bed's project key for the config copy (sync.sh), in ZooKeeper before the scheduler starts, so that Zuul
  # takes it rather than making one: the fixture secret in the copy is encrypted for it. Stored as Zuul stores its own,
  # encrypted with the keystore password (zuul/lib/keystorage.py), and imported as a restore would be.
  "${compose[@]}" up --detach --quiet-pull --wait zookeeper db
  install -m 644 "$here/project-key.pem" "$work/etc/testbed-project-key.pem"
  install -m 644 "$here/import-key.py" "$work/etc/import-key.py"
  "${compose[@]}" run --rm --no-deps --quiet-pull -e TESTBED_CONFIG_REPOSITORY="$config" --entrypoint sh scheduler -c \
    'python3 -I /etc/zuul/import-key.py /etc/zuul/testbed-project-key.pem /tmp/keys.json && zuul-admin import-keys /tmp/keys.json'
fi
# Subscribe before the scheduler can report: every native lifecycle report is captured for the final check.
"${compose[@]}" up --detach --quiet-pull --wait events
"${compose[@]}" exec -T events mosquitto_sub -t aid/zuul/reports -q 1 -i testbed-events > "$work/reports.jsonl" &
reports_pid=$!
"${compose[@]}" up --detach --quiet-pull
"${compose[@]}" ps

# A public test fixture, not a credential: mimic the raw token readable by the PR pod's job user.
# The malicious-hook scenario proves check can read it while check-external executes none of its hooks.
# On both nodes (compose.yaml), as a job may run on either.
for node in node node-2; do
  "${compose[@]}" exec -T --user root "$node" sh -c \
    'mkdir -p /var/run/farm; printf "testbed-farm-credential\n" > /var/run/farm/token; chmod 444 /var/run/farm/token'
done
TESTBED_NODE_ID=$("${compose[@]}" ps --quiet node)
TESTBED_NODE_2_ID=$("${compose[@]}" ps --quiet node-2)
TESTBED_EXECUTOR_ID=$("${compose[@]}" ps --quiet executor)
export TESTBED_NODE_ID TESTBED_NODE_2_ID TESTBED_EXECUTOR_ID

echo "== scenario"
secret_args=()
[ "$secrets" != 1 ] || secret_args=(--secret "$(sed -n 's/^TESTBED_SECRET=//p' "$here/testbed.env")" \
  --project-key "$here/project-key.pem")
# With TESTBED_LOOP_PULL, the two-agent loop's check alone, on that pull request (scenario.py, check 13).
loop=()
if [ -n "${TESTBED_LOOP_PULL:-}" ]; then
  [[ "$TESTBED_LOOP_PULL" =~ ^[0-9]+$ ]] || { echo "TESTBED_LOOP_PULL '$TESTBED_LOOP_PULL' is not a pull request's number" >&2; exit 2; }
  loop=(--loop-pull "$TESTBED_LOOP_PULL")
fi
python3 -I -B "$here/scenario.py" --zuul http://127.0.0.1:9000 --tenant aid --repository "$TESTBED_REPOSITORY" \
  --config-repository "$config" --checkout "$root" "${secret_args[@]}" "${loop[@]}"

if [ "${#loop[@]}" -eq 0 ] && grep -q 'connection "aid-events"' "$root/ci/zuul/testbed/zuul.conf"; then
  python3 -I -B "$here/reports.py" "$work/reports.jsonl"
fi
