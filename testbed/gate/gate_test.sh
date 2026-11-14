#!/usr/bin/env bash
# The test bed's stand-in test (BUILD.testbed), run by aid-bazel-everything in gate. Each pull request of the scenario
# (../scenario.py) says what it does in testbed/gate/<name>.txt, and the test does what every such file in the state
# under test says, so a pull request tested on top of another one does what both say. One instruction a line, each
# until a time (seconds since the epoch), and nothing after it, so that a file merged into main stops acting:
#   <until> sleep <seconds>   waits, so that the scenario sees the builds that run at once
#   <until> fail              fails: a flaky test, until then
# A line that starts with # is a comment.
set -euo pipefail
shopt -s nullglob
now=$(date +%s)
for file in "${TEST_SRCDIR:?}/_main/testbed/gate/"*.txt; do
  name=$(basename "$file")
  while read -r until what value; do
    case "$until" in "" | "#"*) continue ;; esac
    [[ "$until" =~ ^[0-9]+$ ]] || { echo "$name: '$until' is not a time" >&2; exit 2; }
    if [ "$now" -ge "$until" ]; then echo "$name: $what, until $until: over"; continue; fi
    case "$what" in
      sleep) echo "$name: sleep $value"; sleep "$value" ;;
      fail) echo "$name: fail, until $until"; exit 1 ;;
      *) echo "$name: no instruction '$what'" >&2; exit 2 ;;
    esac
  done < "$file"
done
echo "gate_test: passed"
