#!/usr/bin/env bash
# sync.sh: copies the layout into the test bed's two repositories (../README.md, "The test bed"):
#   ci/zuul/testbed/sync.sh /path/to/testbed-checkout /path/to/aid-zuul-config-checkout /path/to/config-testbed-checkout
# The test bed's checkout gets: ci/zuul/; from deploy/zuul/ the zuul.conf, ZooKeeper's configuration, secrets.sh and the
# executor's log script (store-logs.sh, which the base job's post-run runs), and
# the images ThunderBox runs (as testbed.env, with the config copy's repository); what AID's jobs run (a stand-in for
# each script a job names, .bazelversion, and a Bazel workspace whose one test is the gate's stand-in, from workspace/);
# the workflow; and TESTBED, the commits it came from.
# The config copy's checkout gets the aid-zuul-config checkout's files, as they are on disk, plus the test bed's build
# node (testbed/providers.yaml), the test bed's jobs with a secret in place of aid-zuul-config's (below), and the same
# TESTBED. Everything in either checkout but .git is replaced. Commit and push
# both to their main, the config copy first, then dispatch the workflow. The files of record are AIDepartment's and
# aid-zuul-config's; the copies are only for the test. TESTBED_LOOP=1 makes the copies for the two-agent loop's run
# (check 13), which keep gate's requirement of an approving review (below).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
usage() { echo "usage: $0 /path/to/testbed-checkout /path/to/aid-zuul-config-checkout /path/to/config-testbed-checkout" >&2; exit 2; }
[ $# -eq 3 ] && [ -d "$1/.git" ] && [ -d "$2/.git" ] && [ -d "$3/.git" ] || usage
dest=$(cd "$1" && pwd) config=$(cd "$2" && pwd) config_dest=$(cd "$3" && pwd)
for copy in "$dest" "$config_dest"; do
  [ "$copy" != "$root" ] && [ "$copy" != "$config" ] || { echo "$copy is a repository of record, not a test bed's copy" >&2; exit 2; }
done
# Each copy's repository, owner/name, from its checkout's origin.
repository_of() { git -C "$1" remote get-url origin 2> /dev/null | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##'; }
config_repository=$(repository_of "$config_dest")
[[ "$config_repository" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "the config copy's origin is not a GitHub repository" >&2; exit 2; }
testbed_repository=$(repository_of "$dest")
[[ "$testbed_repository" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "the test bed's origin is not a GitHub repository" >&2; exit 2; }

find "$dest" "$config_dest" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
(cd "$config" && find . -mindepth 1 -maxdepth 1 ! -name .git -exec cp -R {} "$config_dest/" \;)
# Compose's node replaces the production k3s provider for the same aid-node label. Keep the
# production base job and playbooks; its real provider is tested on k3s, not Compose. The pipeline
# changes for the workflow's token are below.
rm -f "$config_dest/zuul.d/providers.yaml"
mkdir -p "$config_dest/testbed"
cp "$here/providers.yaml" "$config_dest/testbed/"
# aid-zuul-config's secrets are encrypted for ThunderBox's project key, which the test bed does not have, and Zuul
# decrypts every secret a job names when it loads the configuration. So the copy leaves out zuul.d/secrets.yaml and
# each file of zuul.d/ that defines or names a secret: such a file holds only jobs with secrets, and where they run
# (deploy/zuul/job-secrets.md). The test bed's own jobs with a secret take their place, below.
while IFS= read -r file; do
  rm "$file"
done < <(grep -rlE '^- secret:|^ +secrets:' "$config_dest/zuul.d" || true)
# What the test bed's token cannot do, as patch.py does for Zuul: report check runs (only a GitHub App can) and say
# what permission a pull request's author has (the workflow's token is no collaborator). So the config copy's
# pipelines report commit statuses instead, and start on any author's events. A check run carries a configuration
# error in its summary; a status cannot, so each failure reporter comments too, as main's did before check runs, and
# the scenario's negative cases read the error from that comment. gate's requirement and trigger on check's check run
# become the same on check's status, which GitHub shows as made by github-actions[bot] (its REST API) or
# github-actions (its GraphQL API, which Zuul reads the requirement from). aid-zuul-config's own files keep check runs
# and the permission (AIDepartment's deploy/zuul/github.md); every change must apply, or the copy stops.
pipelines="$config_dest/zuul.d/pipelines.yaml"
# Keep the original before removing permissions for the workflow token. run.sh
# checks its schema and gate's review filters with the pinned scheduler's driver.
cp "$pipelines" "$pipelines.original"
on_check=$(grep -c 'aid-zuul:aid/check:success$' "$pipelines" || true)
TESTBED_CHECK_STATUS="'github-actions(\\[bot\\])?:aid/check:success'" awk '
  BEGIN { status = ENVIRON["TESTBED_CHECK_STATUS"] }
  /^ +- event: check_run$/ { sub(/check_run$/, "pull_request"); print; checkrun = 1; next }
  checkrun && /^ +action: completed$/ { sub(/completed$/, "status"); print; next }
  checkrun && /^ +check: aid-zuul:aid\/check:success$/ { sub(/check: .*/, "status: " status); print; checkrun = 0; next }
  /^ +status: aid-zuul:aid\/check:success$/ { sub(/status: .*/, "status: " status); print; next }
  /^ +check: (in_progress|queued)$/ { sub(/check: .*/, "status: pending"); print; next }
  /^ +check: success$/ { sub(/check: success/, "status: success"); print; next }
  /^ +check: failure$/ { sub(/check: failure/, "status: failure"); print; failed = 1; next }
  /^ +check: cancelled$/ || /^ +permission: write$/ { next }
  failed && /^ +comment: false$/ { sub(/false/, "true") }
  /^ +comment: / { failed = 0 }
  { print }
' "$pipelines" > "$pipelines.testbed" && mv "$pipelines.testbed" "$pipelines"
if grep -qE '^ +(check|permission): |event: check_run|aid-zuul:' "$pipelines"; then
  echo "$pipelines: a check run or a permission the test bed cannot take is left: look at sync.sh again" >&2; exit 1
fi
if [ "$(grep -cF 'github-actions(\[bot\])?:aid/check:success' "$pipelines")" -ne "$on_check" ]; then
  echo "$pipelines: not every requirement or trigger on check's result is on its status: look at sync.sh again" >&2; exit 1
fi
if [ "$(grep -c '^ *status: failure$' "$pipelines")" -ne "$(grep -A1 '^ *status: failure$' "$pipelines" | grep -c '^ *comment: true$')" ]; then
  echo "$pipelines: a failure reporter does not comment, so a configuration error would not be seen" >&2; exit 1
fi
# gate's requirement of an approving review (#1348): the run's token opens every pull request of the scenario, and
# GitHub lets nobody approve their own. So the copy for checks 1 to 12 leaves the requirement out. With TESTBED_LOOP=1
# it keeps it, for check 13 alone: there the builder's pull request is someone else's, which the run's token approves
# (README.md, "The two-agent loop on the test bed").
approvals=$(grep -c '^ *- type: approved$' "$pipelines" || true)
if [ "${TESTBED_LOOP:-}" = 1 ]; then
  [ "$approvals" -eq 1 ] || { echo "$pipelines: gate requires $approvals approving reviews, not one: look at sync.sh again" >&2; exit 1; }
else
  awk '
    /^ +require:$/ { require = 1 }
    /^ +(reject|trigger):$/ { require = 0 }
    require && /^ +review:$/ { match($0, /^ +/); indent = RLENGTH; skip = 1; next }
    skip { match($0, /^ +/); if (RLENGTH > indent) next; skip = 0 }
    { print }
  ' "$pipelines" > "$pipelines.testbed" && mv "$pipelines.testbed" "$pipelines"
  if grep -q '^ *- type: approved$' "$pipelines"; then
    echo "$pipelines: gate still requires an approving review: look at sync.sh again" >&2; exit 1
  fi
fi
mkdir -p "$dest/ci" "$dest/deploy/zuul/server/config" "$dest/deploy/zuul/executor" "$dest/scripts" "$dest/.github/workflows"
cp -R "$root/ci/zuul" "$dest/ci/zuul"
mv "$pipelines.original" "$dest/ci/zuul/testbed/original-pipelines.yaml"
cp "$root/deploy/zuul/server/config/zuul.conf" "$dest/deploy/zuul/server/config/"
cp "$root/deploy/zuul/server/zoo.cfg" "$dest/deploy/zuul/server/"
cp "$root/deploy/zuul/secrets.sh" "$dest/deploy/zuul/"
cp "$root/deploy/zuul/executor/store-logs.sh" "$dest/deploy/zuul/executor/"
# A stand-in for each script a job of AID's names: the test bed's repository is public, and holds only what Zuul needs.
scripts=$(sed -n 's/^      script: //p' "$root/ci/zuul/zuul.d/jobs.yaml")
[ -n "$scripts" ] || { echo "no script in ci/zuul/zuul.d/jobs.yaml: look at sync.sh again" >&2; exit 1; }
for script in $scripts; do
  [[ "$script" =~ ^(ci|scripts)/[A-Za-z0-9._/-]+$ ]] || { echo "'$script' is not a script under ci/ or scripts/" >&2; exit 1; }
  mkdir -p "$dest/$(dirname "$script")"
  printf '%s\n' '#!/usr/bin/env bash' \
    "# The test bed's stand-in for AIDepartment's $script: it shows only that its job ran it." \
    "echo \"stand-in for $script, called with: \$*\"" > "$dest/$script"
  # The gate's plan (ci/zuul/gate.sh plan) names the full job alone: gate runs it, and Zuul skips its other jobs, which
  # need nodes the test bed has not (testbed/providers.yaml).
  if [ "$script" = ci/zuul/gate.sh ]; then
    # shellcheck disable=SC2016 # the stand-in's own text
    printf '%s\n' '[ "${3:-}" != plan ] || { mkdir -p ~/zuul-output/logs; echo '"'"'{"child_jobs": ["aid-bazel-everything"]}'"'"' > ~/zuul-output/logs/gate-plan.json; }' >> "$dest/$script"
  fi
  chmod 755 "$dest/$script"
done
# The test bed's jobs with a secret (secret-probe.yaml), with the shared guard of aid-zuul-config's checkout: a
# project key of the test bed's own, made here and given to Zuul by run.sh, and a fixture secret encrypted for it. Both
# are public in the copies, and guard nothing. A checkout from before the guard runs without them.
secrets=0
secret_value=""
if [ -f "$config_dest/playbooks/secrets/guard.yaml" ]; then
  secrets=1
  secret_value="testbed-secret-$(openssl rand -hex 16)"
  openssl genrsa -out "$dest/ci/zuul/testbed/project-key.pem" 4096 2> /dev/null
  {
    sed "s#@REPOSITORY@#$testbed_repository#g" "$here/secret-probe.yaml"
    echo
    "$root/deploy/zuul/encrypt-secret.sh" testbed-secret value=<(printf '%s' "$secret_value") --project "$config_repository" \
      --public-key <(openssl pkey -in "$dest/ci/zuul/testbed/project-key.pem" -pubout) 2> /dev/null
  } > "$config_dest/testbed/secret-probe.yaml"
  mkdir -p "$config_dest/playbooks/testbed"
  cp "$here/secret-probe-run.yaml" "$config_dest/playbooks/testbed/secret-probe.yaml"
  cp "$here/oidc-probe-run.yaml" "$config_dest/playbooks/testbed/oidc-probe.yaml"
  cat > "$dest/ci/zuul/zuul.d/testbed-post.yaml" <<'POST'
# Added by sync.sh: the test bed's jobs with a secret, in the post pipeline (testbed/secret-probe.yaml).
- project:
    post:
      jobs:
        - testbed-secret-probe
        - testbed-secret-guard-probe
        - testbed-oidc-probe
POST
else
  echo "the aid-zuul-config checkout has no playbooks/secrets/guard.yaml: the test bed's secret checks are left out" >&2
fi
# The Bazel workspace aid-bazel and aid-bazel-everything run in: the version AIDepartment pins, and the gate's test.
cp "$root/.bazelversion" "$dest/"
cp "$here/workspace/MODULE.bazel.testbed" "$dest/MODULE.bazel"
cp "$here/workspace/bazelrc.testbed" "$dest/.bazelrc"
mkdir -p "$dest/testbed/gate"
cp "$here/workspace/BUILD.testbed" "$dest/testbed/gate/BUILD.bazel"
cp "$here/workspace/gate_test.sh" "$dest/testbed/gate/"
cp "$here/workflow.yml" "$dest/.github/workflows/testbed.yml"

# The images, as ThunderBox's manifests pin them: Zuul's from version/, ZooKeeper's and MariaDB's from their own.
zuul_image() {
  local name=quay.io/zuul-ci/zuul-$1 tag digest
  tag=$(awk -v n="$name" '$0 ~ "- name: " n "$" {f=1; next} f && /newTag:/ {print $2; exit}' \
    "$root/deploy/zuul/server/version/kustomization.yaml")
  digest=$(awk -v n="$name" '$0 ~ "- name: " n "$" {f=1; next} f && /digest:/ {print $2; exit}' \
    "$root/deploy/zuul/server/version/kustomization.yaml")
  [ -n "$tag" ] && [ -n "$digest" ] || { echo "no tag and digest for $name in deploy/zuul/server/version" >&2; exit 1; }
  echo "$name:$tag@$digest"
}
pinned() { # pinned <manifest>: its one image
  local image
  image=$(sed -n 's/^ *image: \(.*@sha256:[0-9a-f]*\)$/\1/p' "$root/deploy/zuul/server/$1")
  [ "$(printf '%s\n' "$image" | grep -c .)" -eq 1 ] || { echo "not one pinned image in deploy/zuul/server/$1" >&2; exit 1; }
  echo "$image"
}
{
  echo "# The config project's copy, and the images ThunderBox runs, from deploy/zuul/ (sync.sh)."
  echo "TESTBED_CONFIG_REPOSITORY=$config_repository"
  echo "ZUUL_SCHEDULER_IMAGE=$(zuul_image scheduler)"
  echo "ZUUL_WEB_IMAGE=$(zuul_image web)"
  echo "ZUUL_LAUNCHER_IMAGE=$(zuul_image launcher)"
  echo "ZUUL_EXECUTOR_IMAGE=$(zuul_image executor)"
  echo "ZOOKEEPER_IMAGE=$(pinned zookeeper.yaml)"
  echo "MARIADB_IMAGE=$(pinned database.yaml)"
  echo "# The test bed's jobs with a secret, and their fixture (a test value, not a credential)."
  echo "TESTBED_SECRETS=$secrets"
  echo "TESTBED_SECRET=$secret_value"
} > "$dest/ci/zuul/testbed/testbed.env"

# What each copy came from: each repository's commit, and whether its checkout had changes not committed.
from() { # from <checkout> <repository> [<path>...]
  local dirty
  dirty=$(git -C "$1" status --porcelain -- "${@:3}" | wc -l)
  printf '%s at %s%s' "$2" "$(git -C "$1" rev-parse HEAD)" "$([ "$dirty" -eq 0 ] || echo ", with uncommitted changes")"
}
printf 'Copies of %s and %s, for the Zuul test bed (ci/zuul/README.md)%s.\n' \
  "$(from "$root" "kristofk/AIDepartment ci/zuul" ci/zuul deploy/zuul .bazelversion)" \
  "$(from "$config" kristofk/aid-zuul-config)" \
  "$([ "${TESTBED_LOOP:-}" = 1 ] && echo ", its two-agent loop's run")" | tee "$dest/TESTBED" > "$config_dest/TESTBED"
echo "copied into $dest and $config_dest: $(cat "$dest/TESTBED")"
cat "$dest/ci/zuul/testbed/testbed.env"
