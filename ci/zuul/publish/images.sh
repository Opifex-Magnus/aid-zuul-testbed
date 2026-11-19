#!/usr/bin/env bash
# Zuul's infrastructure images, the job aid-publish-images in post (ci/zuul/publish.md, #1650): on a merge into main
# that changes an image's inputs (ci/zuul/publish/images.mjs), it is built, checked and published through the pusher,
# with the commands gate checks it with (ci/zuul/gate.sh's farm-worker and zuul-node-image):
#
#   zuul-node          aid/zuul-node:ubuntu26.04-<commit>, the PR node's, and aid/zuul-node:post-ubuntu26.04-<commit>,
#                      the post node's and the gate image node's (ci/zuul-node-image.mjs)
#   build-farm-worker  aid/build-farm-worker:<swift>-ubuntu26.04-<commit>, the build farm's workers'
#                      (ci/build-farm-worker.mjs)
#
#   ci/zuul/publish/images.sh build   which images publish; each built, checked, and pushed by digest to the scratch
#                                     registry
#   ci/zuul/publish/images.sh push    each checked image through the pusher (Zuul's token: CI_PUBLISH_TOKEN_FILE); one
#                                     that fails leaves the others published, and fails the job
#
# Nothing deploys them: the summary names each published image by digest, for the admin to apply
# (deploy/zuul/node/README.md, deploy/build-farm/README.md, "Updating the image"). No index records them. What build
# decided and made is kept under $ZUUL_STATE/images/ for push (ci/zuul/publish/apps.sh says why the stages are apart).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# shellcheck source=ci/zuul/publish/job-env.sh
. ci/zuul/publish/job-env.sh

stage=${1:-}
images=$ZUUL_STATE/images

case $stage in
  build)
    mkdir -p "$images"
    for name in zuul-node build-farm-worker; do
      outputs "$images/$name.policy" node ci/zuul/publish/images.mjs "$name"
      [ "$(output "$images/$name.policy" publish)" = true ] || continue
      repository=$(output "$images/$name.policy" repository)
      echo "== $name: build and check"
      # Each line of the list: <repository> <scratch image by digest> <tag>, one per image to publish.
      case $name in
        zuul-node)
          outputs "$images/$name" node ci/zuul-node-image.mjs build
          printf '%s %s %s\n' "$repository" "$(output "$images/$name" image)" "$(output "$images/$name" version)" \
            "$repository" "$(output "$images/$name" post_image)" "$(output "$images/$name" post_version)" >> "$images/list"
          ;;
        build-farm-worker)
          outputs "$images/$name" node ci/build-farm-worker.mjs build
          printf '%s %s %s\n' "$repository" "$(output "$images/$name" image)" "$(output "$images/$name" version)" >> "$images/list"
          ;;
      esac
    done
    if [ -f "$images/list" ]; then
      echo "To publish:"; sed 's/^\([^ ]*\) [^ ]* \(.*\)$/  \1:\2/' "$images/list"
    else
      echo "No infrastructure image's inputs changed, or ci/publisher names another CI: nothing to publish."
    fi
    ;;
  push)
    : "${CI_PUBLISH_TOKEN_FILE:?push needs the pusher token}"
    [ -f "$images/list" ] || { echo "Nothing to publish."; exit 0; }
    export CI_PUBLISH_TIMEOUT_SECONDS=${CI_PUBLISH_TIMEOUT_SECONDS:-3600}
    failed=()
    # Each image on its own: one that fails leaves the others published, and fails the job at the end.
    while read -r repository image version; do
      echo "== $repository:$version: publish"
      if outputs "$images/published" env IMAGE="$image" VERSION="$version" REPOSITORY="$repository" node ci/publish-image.mjs < /dev/null; then
        published=$(output "$images/published" digest)
        echo "Published \`$repository:$version@$published\` (on ThunderBox \`127.0.0.1:5000/$repository:$version@$published\`): pin it by digest where it is deployed." >> "$GITHUB_STEP_SUMMARY"
      else
        failed+=("$repository:$version")
      fi
    done < "$images/list"
    if [ ${#failed[@]} -gt 0 ]; then
      echo "Not published: ${failed[*]} (ci/zuul/publish.md, \"Running a publishing job again\")." >&2
      exit 1
    fi
    ;;
  *) echo "usage: ci/zuul/publish/images.sh build|push" >&2; exit 2 ;;
esac
