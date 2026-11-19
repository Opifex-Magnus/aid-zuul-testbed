#!/bin/sh
# Run inside the executor, before Compose removes its retained build directories.
# Print all outputs, including bootstrap/Ansible errors without job-output failure markers.
set -eu
builds=${1:-/var/lib/zuul/builds}
find "$builds" -maxdepth 4 -type f \( -name 'job-output.txt' -o -name 'ansible*.txt' -o -name 'ansible*.log' \) \
  -exec sh -c 'for output do echo "--- $output"; cat "$output"; done' sh {} +
echo '== retained build file names (including builds without job output)'
find "$builds" -maxdepth 4 -type f
