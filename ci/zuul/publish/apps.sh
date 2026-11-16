#!/usr/bin/env bash
# Zuul's app publishing, the job aid-publish-apps in post (ci/zuul/publish.md): on a merge into main, every application
# whose payload the merge changed is built, checked and published; its declaration goes into the release index.
#
#   ci/zuul/publish/apps.sh build    which apps publish; each one's image built and checked, its declaration written
#   ci/zuul/publish/apps.sh push     each built image through the pusher (Zuul's token: CI_PUBLISH_TOKEN_FILE); an
#                                    app that fails here fails the job in record, once the others are indexed
#   ci/zuul/publish/apps.sh record   each pushed image's declaration into the index (the App's: CI_GITHUB_TOKEN_FILE)
#
# One playbook each, so that each credential is made just before it is used, and only where: Zuul signs the pusher's
# token as push's playbook starts, for at most three hours, and the App's tokens last an hour. What a stage decided and
# made is kept under $ZUUL_STATE/apps/<app>/ for the next. No speculative image is reused: each image is
# built and checked once in this job.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# shellcheck source=ci/zuul/publish/job-env.sh
. ci/zuul/publish/job-env.sh

stage=${1:-}
apps=$ZUUL_STATE/apps

case $stage in
  build)
    rm -rf "$apps"; mkdir -p "$apps"
    trap 'node ci/image-run.mjs cleanup || true' EXIT
    for check in apps/*/ci/check-image.sh; do
      APP=$(basename "$(dirname "$(dirname "$check")")")
      export APP
      # Only the CI that ci/publisher names, and only an app whose payload this merge changed (ci/image-policy.mjs).
      outputs "$apps/policy" node ci/image-policy.mjs "$APP"
      [ "$(output "$apps/policy" publish)" = true ] || continue
      dir=$apps/$APP
      mkdir -p "$dir"
      echo "== $APP: build, check and declare"
      # As gate builds and checks it (ci/zuul/steps.sh), here once more on the merge.
      app_image "$APP" "$dir"
      IMAGE=$(output "$dir/build" image)
      # The declaration first, pinned to the checked digest, which the pusher must return: an image is never
      # published without one (ci/publish-image.mjs).
      node ci/release-app.mjs "$APP" "${IMAGE##*@}"
      cp released/declaration.json "$dir/declaration.json"
      echo "$IMAGE" > "$dir/image"
      echo "$APP" >> "$apps/list"
    done
    [ -f "$apps/list" ] && echo "To publish: $(tr '\n' ' ' < "$apps/list")" || echo "No application's payload changed, or ci/publisher names another CI: nothing to publish."
    ;;
  push)
    : "${CI_PUBLISH_TOKEN_FILE:?push needs the pusher token}"
    [ -f "$apps/list" ] || { echo "Nothing to publish."; exit 0; }
    export CI_PUBLISH_TIMEOUT_SECONDS=${CI_PUBLISH_TIMEOUT_SECONDS:-3600}
    failed=()
    # Each app on its own: one that fails leaves the others published, recorded next, and fails the job at the end.
    while read -r APP; do
      dir=$apps/$APP
      echo "== $APP: publish"
      if outputs "$dir/published" env IMAGE="$(cat "$dir/image")" VERSION="$(output "$dir/source" version)" \
        REPOSITORY="$(output "$dir/source" repository)" node ci/publish-image.mjs; then
        echo "Published $(output "$dir/source" repository)@$(output "$dir/published" digest)." >> "$GITHUB_STEP_SUMMARY"
      else
        rm -f "$dir/published"; failed+=("$APP")
      fi
    done < "$apps/list"
    # Zuul runs the next playbook only after this one passed. So an app that failed here does not fail this stage: it
    # is written down, record indexes every app that did publish, and then fails the job for this one.
    rm -f "$apps/push-failed"
    if [ ${#failed[@]} -gt 0 ]; then
      printf '%s\n' "${failed[@]}" > "$apps/push-failed"
      echo "Not published: ${failed[*]}. The others are recorded next, and the job then fails (ci/zuul/publish.md, \"Running a publishing job again\")." >&2
    fi
    ;;
  record)
    : "${CI_GITHUB_TOKEN_FILE:?record needs the App token}"
    [ -f "$apps/list" ] || { echo "Nothing to record."; exit 0; }
    failed=()
    # Every image push published, also after a push that failed for another app: published without its entry, AID would
    # never offer it (#1286). Not optional: AID finds new versions in the index (contracts/application.md).
    while read -r APP; do
      dir=$apps/$APP
      [ -f "$dir/published" ] || continue
      echo "== $APP: record"
      node ci/release-index.mjs record "$APP" "$(output "$dir/published" digest)" "$dir/declaration.json" || failed+=("$APP")
    done < "$apps/list"
    if [ ${#failed[@]} -gt 0 ]; then
      echo "Published and not recorded: ${failed[*]} (ci/zuul/publish.md, \"Running a publishing job again\")." >&2
    fi
    if [ -f "$apps/push-failed" ]; then
      echo "Not published, in push: $(tr '\n' ' ' < "$apps/push-failed")(ci/zuul/publish.md, \"Running a publishing job again\")." >&2
    fi
    [ ${#failed[@]} -eq 0 ] && [ ! -f "$apps/push-failed" ]
    ;;
  *) echo "usage: ci/zuul/publish/apps.sh build|push|record" >&2; exit 2 ;;
esac
