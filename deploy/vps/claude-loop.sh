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

# Keep the model and timeout configurable without editing this operational file.
CLAUDE_MODEL=${CLAUDE_MODEL:-sonnet}
CLAUDE_TICK_TIMEOUT_SEC=${CLAUDE_TICK_TIMEOUT_SEC:-3300}
timeout --foreground --kill-after=30s "${CLAUDE_TICK_TIMEOUT_SEC}s" \
  env CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 \
  claude --model "$CLAUDE_MODEL" --permission-mode auto -p "/vps-loop-run" --output-format text
CLAUDE_EXIT=$?

gh api repos/TripAndCode/transit-app/dispatches -f event_type=vps-heartbeat >/dev/null 2>&1
exit "$CLAUDE_EXIT"
