#!/usr/bin/env bash
# sync.sh: the GitLab test bed's copy (../../forge.md, "The check on GitLab"), in one GitHub repository of your own
# whose Actions run it:
#   ci/zuul/testbed/gitlab/sync.sh /path/to/gitlab-testbed-checkout /path/to/aid-zuul-config-checkout
# The GitHub test bed's sync.sh (../sync.sh) makes both copies, as it does for that test bed. This puts AID's at the
# checkout's root and the config copy in testbed-config/, without the test bed's jobs with a secret (they are
# encrypted for a project name on GitHub), with this workflow as .github/workflows/gitlab.yml. Everything in the
# checkout but .git is replaced. Commit, push to main, and dispatch: gh workflow run gitlab.yml --repo <owner>/<name>.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../../.." && pwd)
[ $# -eq 2 ] && [ -d "$1/.git" ] && [ -d "$2/.git" ] || { echo "usage: $0 /path/to/gitlab-testbed-checkout /path/to/aid-zuul-config-checkout" >&2; exit 2; }
dest=$(cd "$1" && pwd) config=$(cd "$2" && pwd)
[ "$dest" != "$root" ] && [ "$dest" != "$config" ] || { echo "$dest is a repository of record, not the test bed's copy" >&2; exit 2; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for copy in aid config; do
  git init -q "$tmp/$copy"
  # ../sync.sh reads each copy's owner/name from its origin; GitLab's projects get their own names (scenario.py).
  git -C "$tmp/$copy" remote add origin "https://github.com/gitlab-testbed/$copy"
done
"$here/../sync.sh" "$tmp/aid" "$config" "$tmp/config" > /dev/null
rm -rf "$tmp/config/testbed/secret-probe.yaml" "$tmp/config/playbooks/testbed" "$tmp/aid/ci/zuul/zuul.d/testbed-post.yaml" \
  "$tmp/aid/ci/zuul/testbed/project-key.pem" "$tmp/aid/.github"

find "$dest" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
(cd "$tmp/aid" && find . -mindepth 1 -maxdepth 1 ! -name .git -exec cp -R {} "$dest/" \;)
mkdir -p "$dest/testbed-config" "$dest/.github/workflows"
(cd "$tmp/config" && find . -mindepth 1 -maxdepth 1 ! -name .git -exec cp -R {} "$dest/testbed-config/" \;)
cp "$here/workflow.yml" "$dest/.github/workflows/gitlab.yml"
sed -i 's/^Copies of /GitLab test bed: copies of /' "$dest/TESTBED"
cp "$dest/TESTBED" "$dest/testbed-config/TESTBED"
echo "copied into $dest: $(cat "$dest/TESTBED")"
