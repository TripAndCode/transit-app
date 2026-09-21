#!/usr/bin/env bash
# Run the collector's bash suites, reporting a failure for the set as a whole.
#
# A script rather than an inline Make recipe so the exit behaviour can be
# tested directly: a recipe can only be checked by reading its text, and the
# property that matters here — that one failing suite fails the run — is
# exactly the kind a text check cannot see. Piping the loop, for instance,
# would move the failure flag into a subshell and silently lose it.
#
# Takes the suite directory so a test can point it at a temporary one.
set -uo pipefail

dir="${1:-oracle_cloud/v3/tests}"

shopt -s nullglob
suites=("$dir"/test_*.sh)
shopt -u nullglob

if [ ${#suites[@]} -eq 0 ]; then
  # Not a pass: the caller asked for these suites, so finding none means the
  # directory moved or a glob broke, and reporting success would hide that.
  echo "no suites found in $dir" >&2
  exit 1
fi

fail=0
for t in "${suites[@]}"; do
  echo "── $t ──"
  if bash "$t"; then
    echo "PASS: $t"
  else
    echo "FAIL: $t"
    fail=1
  fi
done

exit "$fail"
