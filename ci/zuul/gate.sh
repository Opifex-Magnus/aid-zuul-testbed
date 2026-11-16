#!/usr/bin/env bash
# The test bed's stand-in for AIDepartment's ci/zuul/gate.sh: it shows only that its job ran it.
echo "stand-in for ci/zuul/gate.sh, called with: $*"
[ "${3:-}" != plan ] || { mkdir -p ~/zuul-output/logs; echo '{"child_jobs": ["aid-bazel-everything"]}' > ~/zuul-output/logs/gate-plan.json; }
