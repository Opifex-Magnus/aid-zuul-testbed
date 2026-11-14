#!/usr/bin/env bash
# sync.sh: copies the layout into the test bed's two repositories (../README.md, "The test bed"):
#   ci/zuul/testbed/sync.sh /path/to/testbed-checkout /path/to/aid-zuul-config-checkout /path/to/config-testbed-checkout
# The test bed's checkout gets: ci/zuul/; from deploy/zuul/ the zuul.conf, ZooKeeper's configuration and secrets.sh, and
# the images ThunderBox runs (as testbed.env, with the config copy's repository); what AID's jobs run (a stand-in for
# the seam rule's script, .bazelversion, an empty MODULE.bazel); the workflow; and TESTBED, the commits it came from.
# The config copy's checkout gets the aid-zuul-config checkout's files, as they are on disk, plus the test bed's build
# node (testbed/providers.yaml) and the same TESTBED. Everything in either checkout but .git is replaced. Commit and push
# both to their main, the config copy first, then dispatch the workflow. The files of record are AIDepartment's and
# aid-zuul-config's; the copies are only for the test.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
usage() { echo "usage: $0 /path/to/testbed-checkout /path/to/aid-zuul-config-checkout /path/to/config-testbed-checkout" >&2; exit 2; }
[ $# -eq 3 ] && [ -d "$1/.git" ] && [ -d "$2/.git" ] && [ -d "$3/.git" ] || usage
dest=$(cd "$1" && pwd) config=$(cd "$2" && pwd) config_dest=$(cd "$3" && pwd)
for copy in "$dest" "$config_dest"; do
  [ "$copy" != "$root" ] && [ "$copy" != "$config" ] || { echo "$copy is a repository of record, not a test bed's copy" >&2; exit 2; }
done
# The config copy's repository, owner/name, from its checkout's origin.
config_repository=$(git -C "$config_dest" remote get-url origin | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')
[[ "$config_repository" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || { echo "the config copy's origin is not a GitHub repository" >&2; exit 2; }

find "$dest" "$config_dest" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
(cd "$config" && find . -mindepth 1 -maxdepth 1 ! -name .git -exec cp -R {} "$config_dest/" \;)
# Compose's node replaces the production k3s provider for the same aid-node label. Keep the
# production base job and playbooks; its real provider is tested on k3s, not Compose. The pipeline
# changes for the workflow's token are below.
rm -f "$config_dest/zuul.d/providers.yaml"
mkdir -p "$config_dest/testbed"
cp "$here/providers.yaml" "$config_dest/testbed/"
# What the test bed's token cannot do, as patch.py does for Zuul: report check runs (only a GitHub App can) and say
# what permission a pull request's author has (the workflow's token is no collaborator). So the config copy's
# pipelines report commit statuses instead, and start on any author's events. A check run carries a configuration
# error in its summary; a status cannot, so each failure reporter comments too, as main's did before check runs, and
# the scenario's negative cases read the error from that comment. aid-zuul-config's own files keep check runs and the
# permission (AIDepartment's deploy/zuul/github.md); every change must apply, or the copy stops.
pipelines="$config_dest/zuul.d/pipelines.yaml"
awk '
  /^ +check: (in_progress|queued)$/ { sub(/check: .*/, "status: pending"); print; next }
  /^ +check: success$/ { sub(/check: success/, "status: success"); print; next }
  /^ +check: failure$/ { sub(/check: failure/, "status: failure"); print; failed = 1; next }
  /^ +check: cancelled$/ || /^ +permission: write$/ { next }
  failed && /^ +comment: false$/ { sub(/false/, "true") }
  /^ +comment: / { failed = 0 }
  { print }
' "$pipelines" > "$pipelines.testbed" && mv "$pipelines.testbed" "$pipelines"
if grep -qE '^ +(check|permission): ' "$pipelines"; then
  echo "$pipelines: a check run or a permission the test bed cannot take is left: look at sync.sh again" >&2; exit 1
fi
if [ "$(grep -c '^ *status: failure$' "$pipelines")" -ne "$(grep -A1 '^ *status: failure$' "$pipelines" | grep -c '^ *comment: true$')" ]; then
  echo "$pipelines: a failure reporter does not comment, so a configuration error would not be seen" >&2; exit 1
fi
mkdir -p "$dest/ci" "$dest/deploy/zuul/server/config" "$dest/scripts" "$dest/.github/workflows"
cp -R "$root/ci/zuul" "$dest/ci/zuul"
cp "$root/deploy/zuul/server/config/zuul.conf" "$dest/deploy/zuul/server/config/"
cp "$root/deploy/zuul/server/zoo.cfg" "$dest/deploy/zuul/server/"
cp "$root/deploy/zuul/secrets.sh" "$dest/deploy/zuul/"
# A stand-in that takes the same arguments: the test bed's repository is public, and holds only what Zuul needs.
cat > "$dest/scripts/seam-lint.sh" <<'STANDIN'
#!/usr/bin/env bash
# The test bed's stand-in for AIDepartment's scripts/seam-lint.sh: it checks only that aid-seam-lint calls it as CI does.
[ "$*" = "--project aid" ] || { echo "seam-lint stand-in: called with '$*', not '--project aid'" >&2; exit 2; }
echo "seam-lint stand-in: called as CI calls the real one"
STANDIN
chmod 755 "$dest/scripts/seam-lint.sh"
cp "$root/.bazelversion" "$dest/"
: > "$dest/MODULE.bazel"
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
} > "$dest/ci/zuul/testbed/testbed.env"

# What each copy came from: each repository's commit, and whether its checkout had changes not committed.
from() { # from <checkout> <repository> [<path>...]
  local dirty
  dirty=$(git -C "$1" status --porcelain -- "${@:3}" | wc -l)
  printf '%s at %s%s' "$2" "$(git -C "$1" rev-parse HEAD)" "$([ "$dirty" -eq 0 ] || echo ", with uncommitted changes")"
}
printf 'Copies of %s and %s, for the Zuul test bed (ci/zuul/README.md).\n' \
  "$(from "$root" "kristofk/AIDepartment ci/zuul" ci/zuul deploy/zuul .bazelversion)" \
  "$(from "$config" kristofk/aid-zuul-config)" | tee "$dest/TESTBED" > "$config_dest/TESTBED"
echo "copied into $dest and $config_dest: $(cat "$dest/TESTBED")"
cat "$dest/ci/zuul/testbed/testbed.env"
