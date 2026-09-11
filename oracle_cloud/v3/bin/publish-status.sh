#!/usr/bin/env bash
# Publish status-snapshot.sh's last document to the VPS operations status
# picture, over HTTPS to GitHub's `repository_dispatch` API -- never SSH, so
# this never needs the Oracle collector's own SSH private key (or any copy of
# it) to exist on the VPS at all. Mirrors the direction this repo already
# uses for the VPS loop's own heartbeat (`deploy/vps/claude-loop.sh` ->
# `gh api repos/:owner/:repo/dispatches` -> `vps-heartbeat-listener.yml`),
# just the other way around: the VPS already has its own separate `gh`
# authentication (used for its PR work) to read `oracle-heartbeat-
# listener.yml`'s run log back out, so nothing new needs to be granted on the
# VPS side either -- only this script's own, narrowly-scoped GitHub token
# (repository_dispatch only, unrelated to any SSH key) is new, and it lives
# only on Oracle.
#
# Authentication: ORACLE_STATUS_GH_TOKEN, a fine-grained personal access
# token scoped to nothing but triggering `repository_dispatch` on this repo.
# Replay resistance: the receiving collector (a future VPS-side reader) never
# lets an older or repeated `observed_at` overwrite a newer one it already
# accepted, so replaying a captured request can at best re-report a fact
# already on record -- it can never make stale data look fresh again. This
# script's own job stops at delivering the document; it does not implement
# that watermark itself.
#
# Leaving ORACLE_STATUS_GH_TOKEN unset is a supported configuration (like
# alert-lib.sh's ALERT_PING_URL): this script exits 0 without publishing
# anything, so a VM that hasn't been given a token yet -- or ever, if the
# channel is intentionally disabled -- still runs its cron job normally
# instead of failing. Every other collector cron job (sync-r2.sh,
# verify-r2.sh, health-check.sh, ...) is completely unaffected by anything
# this script does or fails to do: it is wired into crontab.snippet as its
# own independent line, reads a file those jobs already produce, and writes
# nothing any of them depend on.
set -uo pipefail

BASE_DIR="${COLLECTOR_BASE:-/home/opc/collector}"
STATUS_FILE="${ORACLE_STATUS_FILE:-$BASE_DIR/.status/oracle-crawler-status.json}"
GH_REPO="${ORACLE_STATUS_GH_REPO:-TripAndCode/transit-app}"
EVENT_TYPE="${ORACLE_STATUS_EVENT_TYPE:-oracle-heartbeat}"
CURL="${PUBLISH_STATUS_CURL:-curl}"

if [ -z "${ORACLE_STATUS_GH_TOKEN:-}" ]; then
    echo "publish-status: ORACLE_STATUS_GH_TOKEN is not set — status publishing is disabled, skipping"
    exit 0
fi

if [ ! -f "$STATUS_FILE" ]; then
    echo "publish-status: no status document at $STATUS_FILE (run status-snapshot.sh first) — nothing to publish" >&2
    exit 1
fi

document=$(cat "$STATUS_FILE") || {
    echo "publish-status: could not read $STATUS_FILE" >&2
    exit 1
}
[ -n "$document" ] || {
    echo "publish-status: $STATUS_FILE is empty — nothing to publish" >&2
    exit 1
}

body=$(printf '{"event_type":"%s","client_payload":%s}' "$EVENT_TYPE" "$document")

# --retry covers a blip on the collector VM's own uplink, matching
# alert-lib.sh's alert_report. -f turns any non-2xx response (a revoked
# token, a renamed repo, GitHub itself being down) into a nonzero exit
# instead of a silently-ignored 4xx/5xx body. The token is passed only via
# the Authorization header, never logged or echoed anywhere below.
if ! "$CURL" -fsS -m 15 --retry 3 -o /dev/null \
    -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${ORACLE_STATUS_GH_TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    --data-binary "$body" \
    "https://api.github.com/repos/${GH_REPO}/dispatches"; then
    echo "publish-status: FAILED to publish the status document to ${GH_REPO} (event ${EVENT_TYPE})" >&2
    exit 1
fi

echo "publish-status: published oracle_crawler status to ${GH_REPO} (event ${EVENT_TYPE})"
