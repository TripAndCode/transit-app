#!/usr/bin/env bash
# Remove test-Postgres containers left behind by runs that will not clean up
# after themselves: a hard-cancelled job never reaches its teardown, and a
# merged pull request's scope never runs again to reclaim its own name.
#
# Age is computed here rather than handed to Docker. `docker ps` has no
# `until` filter -- that one belongs to `docker container prune`, and passing
# it to `ps` fails the daemon call outright rather than being ignored. Its
# `before`/`since` filters take a container reference, not a timestamp.
#
# Scoped by label AND by age, and both halves matter: without the label it
# reaps unrelated containers on a shared runner, without the age it kills a
# concurrent job's live database -- the exact failure the per-run naming
# exists to prevent.
#
# Usage: reap-stale.sh <label> <max-age-seconds>
#   DOCKER=<cmd>  override the docker binary (used by the tests' shim)
set -euo pipefail

label="${1:?usage: reap-stale.sh <label> <max-age-seconds>}"
max_age="${2:?usage: reap-stale.sh <label> <max-age-seconds>}"
docker_cmd="${DOCKER:-docker}"

# Epoch conversion goes through python3, not `date -d`: that flag is
# GNU-only, so a BSD/macOS shell silently fails every comparison and the
# sweep quietly reaps nothing. Docker's `.Created` carries nanoseconds and a
# Z suffix, which neither `date` dialect nor fromisoformat parses uniformly,
# so only the fixed-width second-precision prefix is read.
to_epoch() {
  python3 -c '
import datetime, sys
stamp = datetime.datetime.strptime(sys.argv[1][:19], "%Y-%m-%dT%H:%M:%S")
print(int(stamp.replace(tzinfo=datetime.timezone.utc).timestamp()))
' "$1"
}

now="$(date -u +%s)"
cutoff=$((now - max_age))

removed=0
for id in $("$docker_cmd" ps -aq --filter "label=$label"); do
  created="$("$docker_cmd" inspect -f '{{.Created}}' "$id" 2>/dev/null)" || continue
  [ -n "$created" ] || continue
  ts="$(to_epoch "$created" 2>/dev/null)" || continue
  if [ "$ts" -lt "$cutoff" ]; then
    echo "reaping abandoned test-postgres container $id (created $created)"
    "$docker_cmd" rm -f -v "$id" >/dev/null
    removed=$((removed + 1))
  fi
done

echo "reaped $removed abandoned container(s)"
