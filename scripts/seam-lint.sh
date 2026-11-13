#!/usr/bin/env bash
# The test bed's stand-in for AIDepartment's scripts/seam-lint.sh: it checks only that aid-seam-lint calls it as CI does.
[ "$*" = "--project aid" ] || { echo "seam-lint stand-in: called with '$*', not '--project aid'" >&2; exit 2; }
echo "seam-lint stand-in: called as CI calls the real one"
