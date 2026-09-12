#!/usr/bin/env bash
set -uo pipefail

exec 200>/tmp/claude-loop.lock
if ! flock -n 200; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ): previous run still in progress, skipping this tick"
  exit 0
fi

cd /root/transit-app || exit 1
export HOME=/root

SETTINGS_SOURCE=/root/transit-app-settings.local.json
SETTINGS_TARGET=/root/transit-app/.claude/settings.local.json
WORKTREE_HOOK_SOURCE=/root/transit-app-post-checkout
WORKTREE_HOOK_TARGET=/root/transit-app/.git/hooks/post-checkout
SHARED_NODE_MODULES=/root/transit-app/frontend/node_modules

if [[ ! -f "$SETTINGS_SOURCE" || ! -x "$WORKTREE_HOOK_SOURCE" ]]; then
  echo "VPS loop bootstrap source missing; refusing to run without permissions/worktree provisioning"
  exit 1
fi
if [[ ! -e "$SETTINGS_TARGET" ]]; then
  install -m 0600 "$SETTINGS_SOURCE" "$SETTINGS_TARGET" || exit 1
elif ! cmp -s "$SETTINGS_SOURCE" "$SETTINGS_TARGET"; then
  echo "VPS settings drift detected at $SETTINGS_TARGET; inspect and reconcile it by hand"
  exit 1
fi
if [[ ! -e "$WORKTREE_HOOK_TARGET" ]]; then
  install -m 0755 "$WORKTREE_HOOK_SOURCE" "$WORKTREE_HOOK_TARGET" || exit 1
elif ! cmp -s "$WORKTREE_HOOK_SOURCE" "$WORKTREE_HOOK_TARGET"; then
  echo "VPS post-checkout hook drift detected; inspect and reconcile it by hand"
  exit 1
fi
if [[ ! -d "$SHARED_NODE_MODULES" ]]; then
  echo "Shared frontend/node_modules missing; provision it before the VPS loop can verify frontend work"
  exit 1
fi

# Mandatory local secret scanning: git worktrees share one .git/hooks
# directory, so this check (and lazy install) against the persistent
# clone's common git dir covers every worker worktree the loop cuts below,
# without each dispatched worker needing its own install permissions.
# --check is the same three-part verification (executable + pre-commit
# marker + no core.hooksPath override) install_hook itself uses, so this
# can't silently drift into a narrower check than what "installed" means.
if ! bash scripts/setup_git_hooks.sh --check >/dev/null 2>&1; then
  echo "gitleaks pre-commit hook missing or invalid; installing via scripts/setup_git_hooks.sh"
  if ! bash scripts/setup_git_hooks.sh; then
    echo "Could not install the mandatory gitleaks pre-commit hook; refusing to run without local secret-scanning protection"
    exit 1
  fi
fi

# Keep the model and timeout configurable without editing this operational file.
CLAUDE_MODEL=${CLAUDE_MODEL:-sonnet}
CLAUDE_TICK_TIMEOUT_SEC=${CLAUDE_TICK_TIMEOUT_SEC:-3300}
timeout --foreground --kill-after=30s "${CLAUDE_TICK_TIMEOUT_SEC}s" \
  env CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 \
  claude --model "$CLAUDE_MODEL" --permission-mode auto -p "/vps-loop-run" --output-format text
CLAUDE_EXIT=$?

# Fold vps-loop-run's own progress signal into the heartbeat: scripts/vps_loop_health.py
# parses NEXT_TASK.md's Status log for last_successful_tick/current_item/blocker_class/
# paused state and the repeated_without_progress/stale_pause alert flags. Pre-declare
# defaults before eval'ing its --format shell output so a parse error there (it fails
# closed, non-zero exit, empty stdout) never leaves a variable unset under this script's
# own `set -u` further down.
VPS_LOOP_LAST_SUCCESSFUL_TICK=""
VPS_LOOP_CURRENT_ITEM=""
VPS_LOOP_BLOCKER_CLASS=""
VPS_LOOP_PAUSED="false"
VPS_LOOP_PAUSED_SINCE=""
VPS_LOOP_REPEATED_WITHOUT_PROGRESS="false"
VPS_LOOP_STALE_PAUSE="false"
HEALTH_SHELL_OUTPUT=$(python3 scripts/vps_loop_health.py --repo /root/transit-app \
  --out /root/vps-loop-health.json --format shell 2>/root/vps-loop-health.err) || true
eval "$HEALTH_SHELL_OUTPUT"

gh api repos/TripAndCode/transit-app/dispatches \
  -f event_type=vps-heartbeat \
  -F "client_payload[paused]=$VPS_LOOP_PAUSED" \
  -F "client_payload[repeated_without_progress]=$VPS_LOOP_REPEATED_WITHOUT_PROGRESS" \
  -F "client_payload[stale_pause]=$VPS_LOOP_STALE_PAUSE" \
  -F "client_payload[blocker_class]=$VPS_LOOP_BLOCKER_CLASS" \
  -F "client_payload[current_item]=$VPS_LOOP_CURRENT_ITEM" \
  -F "client_payload[last_successful_tick]=$VPS_LOOP_LAST_SUCCESSFUL_TICK" \
  -F "client_payload[paused_since]=$VPS_LOOP_PAUSED_SINCE" \
  >/dev/null 2>&1
exit "$CLAUDE_EXIT"
