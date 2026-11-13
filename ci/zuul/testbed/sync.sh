#!/usr/bin/env bash
# sync.sh: copies the layout of ci/zuul/ into a checkout of a test bed's repository (../README.md, "The test bed"):
#   ci/zuul/testbed/sync.sh /path/to/testbed-checkout
# Everything in the checkout but .git is replaced by: ci/zuul/; from deploy/zuul/ the zuul.conf, ZooKeeper's
# configuration and secrets.sh, and the images ThunderBox runs (as testbed.env); what AID's jobs run (a stand-in for the
# seam rule's script, .bazelversion, an empty MODULE.bazel); the workflow; and TESTBED, the AIDepartment commit it came
# from.
# Commit and push it to the test bed's main, then dispatch the workflow. The files of record are these; the copy is
# only for the test.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
[ $# -eq 1 ] && [ -d "$1/.git" ] || { echo "usage: $0 /path/to/testbed-checkout" >&2; exit 2; }
dest=$(cd "$1" && pwd)
[ "$dest" != "$root" ] || { echo "$dest is this repository, not a test bed's" >&2; exit 2; }

find "$dest" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
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
  echo "# The images ThunderBox runs, from deploy/zuul/ (sync.sh)."
  echo "ZUUL_SCHEDULER_IMAGE=$(zuul_image scheduler)"
  echo "ZUUL_WEB_IMAGE=$(zuul_image web)"
  echo "ZUUL_LAUNCHER_IMAGE=$(zuul_image launcher)"
  echo "ZUUL_EXECUTOR_IMAGE=$(zuul_image executor)"
  echo "ZOOKEEPER_IMAGE=$(pinned zookeeper.yaml)"
  echo "MARIADB_IMAGE=$(pinned database.yaml)"
} > "$dest/ci/zuul/testbed/testbed.env"

commit=$(git -C "$root" rev-parse HEAD)
dirty=$(git -C "$root" status --porcelain -- ci/zuul deploy/zuul .bazelversion | wc -l)
printf 'A copy of kristofk/AIDepartment ci/zuul at %s%s, for its Zuul test bed (ci/zuul/README.md).\n' "$commit" \
  "$([ "$dirty" -eq 0 ] || echo ", with uncommitted changes")" > "$dest/TESTBED"
echo "copied into $dest: $(cat "$dest/TESTBED")"
cat "$dest/ci/zuul/testbed/testbed.env"
