#!/usr/bin/env bash
# Zuul's AID release, the job aid-release in post (ci/zuul/publish.md): a merge into main that changes VERSION releases
# it through build, push and record stages. A merge that leaves
# VERSION alone, or one while ci/publisher names another CI, does nothing (ci/aid-release.mjs plan).
#
#   ci/zuul/publish/release.sh build    the plan; the release folder, built clean (#1298); the three images, built
#                                       and checked; the keychain asset and the entry, written
#   ci/zuul/publish/release.sh push     the images through the pusher (Zuul's token: CI_PUBLISH_TOKEN_FILE), once the
#                                       version is still unreleased (the App's token, used first)
#   ci/zuul/publish/release.sh record   the GitHub Release aid-v<version>, then its entry in the index (the App's token)
#   ci/zuul/publish/release.sh report   after a failure: open or comment on the release's issue
#
# One playbook each, so that each credential is made just before it is used (ci/zuul/publish/apps.sh says why). What a
# stage decided and made is kept under $ZUUL_STATE/release/ for the next.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# shellcheck source=ci/zuul/publish/job-env.sh
. ci/zuul/publish/job-env.sh

stage=${1:-}
state=$ZUUL_STATE/release
released=$state/released

case $stage in
  build)
    mkdir -p "$released"
    outputs "$state/plan" node ci/aid-release.mjs plan
    # A plan that ran to its end, so report knows a failure after it from a refusal by it (which writes no plan).
    touch "$state/planned"
    [ "$(output "$state/plan" release)" = true ] || { echo "Nothing to release."; exit 0; }
    VERSION=$(output "$state/plan" version) COMMIT=$(output "$state/plan" commit)
    export VERSION COMMIT
    node ci/aid-release.mjs unreleased
    trap 'node ci/image-run.mjs cleanup || true' EXIT
    # Clean: no cache, no farm (deploy/release-folder.sh). Every release carries the keychain asset from it.
    deploy/release-folder.sh
    node ci/aid-release.mjs keychain dist/keychain/aid-keychain "$released"
    # As gate builds and checks them (ci/zuul/steps.sh).
    aid_images "$state" "$released/checks.txt"
    # The entry first, pinned to the checked digests, which the pusher must return: no image is published without it.
    node ci/aid-release.mjs entry "$released/checks.txt" "$released/aid-release.json"
    printf 'aid=%s\naid-broker=%s\naid-ingress=%s\n' "$IMAGE_AID" "$IMAGE_AID_BROKER" "$IMAGE_AID_INGRESS" > "$state/images"
    echo "Built and checked AID $VERSION from $COMMIT." >> "$GITHUB_STEP_SUMMARY"
    ;;
  push)
    : "${CI_PUBLISH_TOKEN_FILE:?push needs the pusher token}" "${CI_GITHUB_TOKEN_FILE:?push needs the App token, to ask once more}"
    [ -f "$state/images" ] || { echo "Nothing to release."; exit 0; }
    # Asked again right before the pusher: a version released once is never pushed again.
    node ci/aid-release.mjs unreleased
    # The tag the pusher gives each image: the version the plan released, from build's state (each stage is a process
    # of its own).
    VERSION=$(output "$state/plan" version)
    [ -n "$VERSION" ] || { echo "push: build's plan names no version" >&2; exit 1; }
    export VERSION
    export CI_PUBLISH_TIMEOUT_SECONDS=${CI_PUBLISH_TIMEOUT_SECONDS:-3600}
    rm -f "$state/digests"
    for name in aid aid-broker aid-ingress; do
      outputs "$state/published-$name" env IMAGE="$(output "$state/images" "$name")" REPOSITORY="aid/$name" node ci/publish-image.mjs
      echo "$name=$(output "$state/published-$name" digest)" >> "$state/digests"
    done
    echo "Published aid, aid-broker and aid-ingress $(output "$state/plan" version) by their checked digests." >> "$GITHUB_STEP_SUMMARY"
    ;;
  record)
    : "${CI_GITHUB_TOKEN_FILE:?record needs the App token}"
    [ -f "$state/digests" ] || { echo "Nothing to release."; exit 0; }
    # Asked again right before the release: a version released once is never released again.
    node ci/aid-release.mjs unreleased
    node ci/zuul/publish/github.mjs release "$released"
    # Not optional: AID's updates find releases in the index (contracts/application.md).
    node ci/aid-index.mjs record "$released/aid-release.json" \
      "$(output "$state/digests" aid)" "$(output "$state/digests" aid-broker)" "$(output "$state/digests" aid-ingress)"
    ;;
  report)
    # Every failure of the job opens or comments on the release's issue on main: a refusal by the plan too (a version released already, CHANGELOG.md), which leaves no plan. Not
    # when the plan ran to its end and released nothing, nor while ci/publisher names another CI.
    if [ -f "$state/planned" ] && [ "$(output "$state/plan" release)" != true ]; then
      echo "The plan released nothing: nothing to report."; exit 0
    fi
    # A build that failed before its plan may have left no state folder.
    mkdir -p "$state"
    outputs "$state/publisher" node ci/publisher.mjs
    [ "$(output "$state/publisher" publisher)" = true ] || { echo "ci/publisher names another CI: it reports."; exit 0; }
    version=$(output "$state/plan" version 2>/dev/null || true)
    node ci/zuul/publish/github.mjs report "${version:-$(tr -d '[:space:]' < VERSION)}"
    ;;
  *) echo "usage: ci/zuul/publish/release.sh build|push|record|report" >&2; exit 2 ;;
esac
