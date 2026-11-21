# shellcheck shell=bash
# The steps Zuul's jobs share (#1521), sourced from the checkout's root: gate builds and checks the images that post
# builds, checks and publishes (ci/zuul/publish.md), with the same commands, so a pull request's image is checked as its
# merge's will be. Gate's (ci/zuul/gate.sh) stop at the scratch registry; post's (ci/zuul/publish/apps.sh, release.sh)
# go on to the pusher. The commands are the GitHub Actions workflows' (app-image.yml, aid-images.yml).

# One step's outputs, as a workflow step's `>> "$GITHUB_OUTPUT"`: `outputs <file> <command...>` empties <file>, and runs
# the command with GITHUB_OUTPUT pointing at it and its standard output appended to it (and shown in the log).
outputs() { local file=$1; shift; : > "$file"; GITHUB_OUTPUT=$file "$@" | tee -a "$file"; }
# A key's value in such a file.
output() { sed -n "s/^$2=//p" "$1" | tail -n 1; }

# app_image <app> <folder>: the application's image built and structurally tested with Bazel rules_oci into the scratch registry, started as AID starts
# it for its acceptance test (apps/<app>/ci/check-image.sh), and recorded. <folder>/source holds what ci/app-image.mjs
# read, <folder>/build the image by digest (`image=`).
app_image() {
  local app=$1 dir=$2 image limits
  if [[ ! $app =~ ^[a-z][a-z0-9-]*$ ]] || [ ! -f "apps/$app/ci/check-image.sh" ]; then
    echo "'$app' is not an application with an image check (apps/<app>/ci/check-image.sh)" >&2
    return 1
  fi
  mkdir -p "$dir"
  outputs "$dir/source" node ci/app-image.mjs "$app"
  outputs "$dir/build" node ci/build-image.mjs "apps/$app" "$app"
  image=$(output "$dir/build" image)
  limits=$(node ci/container-limits.mjs "apps/$app/declaration.json" deploy/thunderbox/policy.json "$ARC")
  APP=$app IMAGE=$image APP_LIMIT_ARGS=$limits IMAGE_RUN=$PWD/ci/image-run.mjs bash "apps/$app/ci/check-image.sh"
  APP=$app IMAGE=$image node ci/image-summary.mjs "$app"
}

# aid_images <folder> <checks>: AID's three images built from the release folder in dist/ (deploy/release-folder.sh)
# and each checked where the broker would run it, the checks written to <checks>. <folder>/<name> holds each image by
# digest, and IMAGE_AID, IMAGE_AID_BROKER and IMAGE_AID_INGRESS are exported.
aid_images() {
  local dir=$1 checks=$2 name
  mkdir -p "$dir"
  for name in aid aid-broker aid-ingress; do
    outputs "$dir/$name" node ci/aid-release.mjs build "$name"
  done
  IMAGE_AID=$(output "$dir/aid" image) IMAGE_AID_BROKER=$(output "$dir/aid-broker" image)
  IMAGE_AID_INGRESS=$(output "$dir/aid-ingress" image)
  export IMAGE_AID IMAGE_AID_BROKER IMAGE_AID_INGRESS
  node ci/aid-release.mjs check "$checks"
}
