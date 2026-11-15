#!/bin/sh
# store-logs.sh SOURCE BUILD: copies one build's logs into Zuul's log store, and prints the address they are served at
# (web.md, "The logs"). The base job's last post-run playbook runs it on the executor, as a trusted playbook, which sees
# the store (zuul.conf, trusted_rw_paths) and this script (trusted_ro_paths):
#   SOURCE  the build's log directory on the executor, zuul.executor.log_root
#   BUILD   the build's UUID, zuul.build
#
# The store (ZUUL_LOG_STORE, default /srv/zuul-logs) has a directory per day, in UTC, and in it one per build:
# <day>/<build>/. prune-logs.sh removes whole days. The address is a path, /logs/<day>/<build>/, on the origin that
# serves both zuul-web and the store: AID's Zuul UI for a person, the forwarder's zuul-logs for an AID app. So it names
# no host, and works from both.
#
# Only directories and regular files are copied: a symbolic link or a special file a job left is not, so nothing the
# store serves points out of it. Everything is made readable by all, since the log server runs as another user than
# the executor. The copy is made under a hidden name and renamed when complete; the log server hides such names.
set -eu

store=${ZUUL_LOG_STORE:-/srv/zuul-logs}
[ $# -eq 2 ] || { echo "usage: store-logs.sh SOURCE BUILD" >&2; exit 2; }
source=$1
build=$2
# Zuul's build UUIDs are 32 lower-case hex digits; nothing else may become a path here.
case "$build" in
  *[!0-9a-f]*|"") echo "store-logs.sh: not a build UUID: $build" >&2; exit 2 ;;
esac
[ "${#build}" -eq 32 ] || { echo "store-logs.sh: not a build UUID: $build" >&2; exit 2; }
[ -d "$source" ] || { echo "store-logs.sh: no log directory $source" >&2; exit 1; }
[ -d "$store" ] || { echo "store-logs.sh: no log store $store" >&2; exit 1; }

day=$(date -u +%Y-%m-%d)
dest=$store/$day/$build
partial=$store/$day/.$build.partial
[ ! -e "$dest" ] || { echo "store-logs.sh: $dest exists already" >&2; exit 1; }
mkdir -p "$store/$day"
chmod 755 "$store/$day"
rm -rf "$partial"
mkdir "$partial"
# Every mkdir and cp must succeed, or nothing is published: a file cut short (the quota reached, a file it cannot read)
# would otherwise be served as the build's log. Each inner shell stops at its first failure (a loop's status is only its
# last command's), find then fails, and the chain with it; the partial copy goes. A chain of &&, not set -e, which an
# if's condition turns off.
# shellcheck disable=SC2016 # $0 and $@ are the inner shells'
if ! (cd "$source" \
      && find . -type d -exec sh -c 'for d in "$@"; do mkdir -p "$0/$d" || exit 1; done' "$partial" {} + \
      && find . -type f -exec sh -c 'for f in "$@"; do cp -p "$f" "$0/$f" || exit 1; done' "$partial" {} + \
      && chmod -R u=rwX,go=rX "$partial"); then
  rm -rf "$partial"
  echo "store-logs.sh: the copy into $partial failed; nothing is stored" >&2
  exit 1
fi
mv "$partial" "$dest"
echo "/logs/$day/$build/"
