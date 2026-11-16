# shellcheck shell=bash
# Sourced by the scripts of Zuul's publishing jobs (ci/zuul/publish.md), from the checkout's root on the post node: the
# environment AID's release scripts read, from what Zuul's trusted playbook gives them. Those scripts were written for
# GitHub Actions, and read its variables; here they get the same meaning from Zuul's, in this one place, so that each
# step runs the same command on both CIs (#1341's rule).
#
# From the playbook (aid-zuul-config's playbooks/publish/): ZUUL_PIPELINE, ZUUL_OLDREV, ZUUL_NEWREV, ZUUL_BUILD,
# ZUUL_BUILD_URL, ZUUL_STATE (a folder that outlives the job's playbooks), the post node's own BuildKit as BUILDKIT_HOST
# (deploy/zuul/post-buildkit/), and ThunderBox's endpoints as the image runners have them: CI_SCRATCH_REGISTRY,
# CI_RUN_NAMESPACE, CI_PUBLISH_URL, CI_PUBLISH_AUDIENCE. Each
# stage gets only the credential it uses, made as its playbook starts: CI_GITHUB_TOKEN_FILE, the App aid-release's token
# (an hour), in the stages that read or write GitHub (the release's push too, which asks GitHub once before it pushes);
# CI_PUBLISH_TOKEN_FILE, Zuul's token for the pusher (three hours), in the stage that pushes and in no other.
set -euo pipefail

for name in ZUUL_PIPELINE ZUUL_OLDREV ZUUL_NEWREV ZUUL_BUILD ZUUL_BUILD_URL ZUUL_STATE; do
  [ -n "${!name:-}" ] || { echo "job-env: $name is required" >&2; exit 2; }
done
# Only a push to main: post's items are refs, never a proposed change (the guard has said so already, and this holds
# for each script on its own).
[ "$ZUUL_PIPELINE" = post ] || { echo "job-env: publishing runs only in post, not $ZUUL_PIPELINE" >&2; exit 2; }
[[ $ZUUL_NEWREV =~ ^[0-9a-f]{40}$ && $ZUUL_OLDREV =~ ^[0-9a-f]{40}$ ]] || { echo "job-env: the push has no commit range" >&2; exit 2; }
[ "$(git rev-parse HEAD)" = "$ZUUL_NEWREV" ] || { echo "job-env: the checkout is not $ZUUL_NEWREV" >&2; exit 2; }

export CI_SYSTEM=zuul
export CI_RUNNER=self-hosted ARC=true
export GITHUB_REPOSITORY=kristofk/AIDepartment GITHUB_SERVER_URL=https://github.com
export GITHUB_REF=refs/heads/main GITHUB_EVENT_NAME=push GITHUB_SHA=$ZUUL_NEWREV
# The build is the run: its UUID names the scratch images and labels the image-check pods (ci/image-run.mjs).
export GITHUB_RUN_ID=$ZUUL_BUILD GITHUB_RUN_ATTEMPT=1 RUNNER_NAME=zuul-$ZUUL_BUILD
export CI_RUN_URL=$ZUUL_BUILD_URL
mkdir -p "$ZUUL_STATE/tmp" "$HOME/zuul-output/logs"
export RUNNER_TEMP=$ZUUL_STATE/tmp TMPDIR=$ZUUL_STATE/tmp
printf '{"before":"%s"}\n' "$ZUUL_OLDREV" > "$ZUUL_STATE/event.json"
export GITHUB_EVENT_PATH=$ZUUL_STATE/event.json
# What a step writes for the next (key=value lines) goes to a file of its own; the summary is kept with the build's logs.
export GITHUB_STEP_SUMMARY=$HOME/zuul-output/logs/summary.md
# Node's fetch through the node's proxy (#208).
export NODE_USE_ENV_PROXY=1
# The cluster's API, for the image check's kubectl (ci/image-run.mjs) with the post node's mounted ServiceAccount. The
# pod has these, but sshd gives a job's session only its one SetEnv line (deploy/zuul/node/image/sshd_config), so
# kubectl found no in-cluster config and tried localhost:8080 (#1622). By name, not the pod's IP: NO_PROXY's .svc lets
# the name past the node's proxy, which refuses the IP.
export KUBERNETES_SERVICE_HOST=kubernetes.default.svc KUBERNETES_SERVICE_PORT=443
# Commits to the index branch are the App's.
export CI_COMMIT_NAME="aid-release[bot]" CI_COMMIT_EMAIL="aid-release[bot]@users.noreply.github.com"

# origin is GitHub, with the App's token when the stage has one: the policy and the plan fetch the push's base, and the
# index branch is pushed there. git reads the token from its file each time; it is never in the configuration or on a
# command line. A stage without it has no credential for GitHub at all.
git remote remove origin 2>/dev/null || true
# AID_GIT_REMOTE only for ci/zuul-publish.test.mjs, which pushes the index to a repository of its own.
git remote add origin "${AID_GIT_REMOTE:-https://github.com/kristofk/AIDepartment.git}"
git config --unset-all credential.https://github.com.helper 2>/dev/null || true
if [ -n "${CI_GITHUB_TOKEN_FILE:-}" ]; then
  # shellcheck disable=SC2016 # expanded by git's shell, when it asks
  git config credential.https://github.com.helper \
    '!f() { [ "$1" = get ] || exit 0; echo username=x-access-token; printf "password=%s\n" "$(cat "$CI_GITHUB_TOKEN_FILE")"; }; f'
fi

# outputs and output, and the image steps gate shares with these jobs.
# shellcheck source=ci/zuul/steps.sh
. ci/zuul/steps.sh
