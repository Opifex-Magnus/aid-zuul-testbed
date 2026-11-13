#!/usr/bin/env bash
# run.sh: the test bed (../README.md, "The test bed"). Starts Zuul in Docker Compose with the layout of ci/zuul/, then
# runs scenario.py against it, and prints the services' logs if anything fails. It runs in the test bed's workflow
# (workflow.yml), in a copy that sync.sh made, on a machine with Docker and passwordless sudo:
#   GITHUB_TOKEN=... TESTBED_REPOSITORY=owner/name ci/zuul/testbed/run.sh
# GITHUB_TOKEN must be able to push branches, open and close pull requests and set commit statuses in the test bed's
# repository, which must be public: the config project is read from it without a token.
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
}
finish() {
  local status=$?
  [ "$status" -eq 0 ] || logs
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
export ZUUL_KEYSTORE_PASSWORD ZUUL_DB_PASSWORD DB_ROOT_PASSWORD ZUUL_WEBHOOK_TOKEN ZUUL_GITHUB_TOKEN

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
mkdir -p "$work/zookeeper-tls" "$work/client-tls" "$work/etc" "$work/ssh"
unpack zookeeper-tls keystore.pem "$work/zookeeper-tls/keystore.pem"
unpack zookeeper-tls ca.crt "$work/zookeeper-tls/ca.crt"
for key in tls.crt tls.key ca.crt; do unpack zuul-zookeeper-client "$key" "$work/client-tls/$key"; done
# The services run as their images' users, ZooKeeper's 1000 and Zuul's 10001; these keys are the run's own.
chmod -R a+rX "$work/zookeeper-tls" "$work/client-tls"
ssh-keygen -q -t ed25519 -N '' -C zuul-testbed -f "$work/ssh/id_rsa"
chmod 644 "$work/ssh/id_rsa.pub"
sudo chown -R 10001:10001 "$work/ssh"

echo "== configuration"
# The deployment's zuul.conf, plus the test bed's connections; the deployment's tenant file is replaced by the test
# bed's, with the repository put in.
cat "$root/deploy/zuul/server/config/zuul.conf" "$here/zuul.conf" > "$work/etc/zuul.conf"
sed "s#@REPOSITORY@#$TESTBED_REPOSITORY#g" "$here/main.yaml" > "$work/etc/main.yaml"
chmod -R a+rX "$work/etc"

echo "== start"
"${compose[@]}" build --quiet node
"${compose[@]}" up --detach --quiet-pull
"${compose[@]}" ps

echo "== scenario"
python3 -I -B "$here/scenario.py" --zuul http://127.0.0.1:9000 --tenant aid --repository "$TESTBED_REPOSITORY" \
  --checkout "$root"
