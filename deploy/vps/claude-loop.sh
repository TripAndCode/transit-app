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
# --check is the same verification install_hook itself uses (executable +
# pre-commit marker + no core.hooksPath override + unqualified `gitleaks`
# on PATH still resolving to the pinned GITLEAKS_VERSION), so this can't
# silently drift into a narrower check than what "installed" means -- in
# particular it still catches a different-version gitleaks that starts
# shadowing the install dir on PATH sometime after this VPS clone's last
# successful install.
if ! bash scripts/setup_git_hooks.sh --check >/dev/null 2>&1; then
  echo "gitleaks pre-commit hook missing or invalid; installing via scripts/setup_git_hooks.sh"
  if ! bash scripts/setup_git_hooks.sh; then
    echo "Could not install the mandatory gitleaks pre-commit hook; refusing to run without local secret-scanning protection"
    exit 1
  fi
fi

# --- Guarded continuation (item 128) -----------------------------------------
#
# One systemd-timer firing used to run exactly one /vps-loop-run tick, so a
# successful merge/cleanup still had to wait for the next scheduled firing
# (up to a full timer interval) before the loop could pick up the next
# actionable item. This section instead chains ticks back to back, within
# this one invocation, for as long as each tick keeps making real progress --
# and stops (scheduling a bounded backoff via scripts/vps_loop_chain_state.py
# so the *next* invocation, whether from the timer or a manual re-run, skips
# a no-op retry until the backoff window clears) the moment a tick is
# blocked, failed, ambiguous, or finds nothing actionable. See
# scripts/vps_loop_chain_state.py's own module docstring for the full state
# machine and `.claude/README.md`'s VPS operations section for the runbook.
#
# Keep the model, per-tick timeout, and every chain-control knob configurable
# without editing this operational file.
CLAUDE_MODEL=${CLAUDE_MODEL:-sonnet}
CLAUDE_TICK_TIMEOUT_SEC=${CLAUDE_TICK_TIMEOUT_SEC:-3300}
CLAUDE_LOOP_CHAIN_STATE_FILE=${CLAUDE_LOOP_CHAIN_STATE_FILE:-/root/vps-loop-chain-state.json}
CLAUDE_LOOP_MAX_CHAIN_TICKS=${CLAUDE_LOOP_MAX_CHAIN_TICKS:-5}
CLAUDE_LOOP_MAX_CHAIN_WALLCLOCK_SEC=${CLAUDE_LOOP_MAX_CHAIN_WALLCLOCK_SEC:-14400}
CLAUDE_LOOP_CHAIN_DELAY_SEC=${CLAUDE_LOOP_CHAIN_DELAY_SEC:-5}
CLAUDE_LOOP_BACKOFF_BASE_SEC=${CLAUDE_LOOP_BACKOFF_BASE_SEC:-300}
CLAUDE_LOOP_BACKOFF_CAP_SEC=${CLAUDE_LOOP_BACKOFF_CAP_SEC:-3600}
# An in-flight tick can never legitimately run longer than its own timeout
# (plus the `timeout` command's own kill-after grace); a flag still marked
# in_progress well past that can only mean the previous wrapper process
# itself was killed (systemd's TimeoutStartSec, an OOM kill, a reboot)
# before it reached `record-outcome`.
CLAUDE_LOOP_MAX_STALE_AGE_SEC=${CLAUDE_LOOP_MAX_STALE_AGE_SEC:-$((CLAUDE_TICK_TIMEOUT_SEC + 300))}

CHAIN_STATE_SCRIPT="scripts/vps_loop_chain_state.py"

# Re-check the gate (bounded backoff + stale-lock recovery) before this
# invocation does anything else. The single-flight `flock` above already
# guarantees no second `claude-loop.sh` process is running concurrently;
# this is the separate, wrapper-owned guard that stops a *new* invocation
# from immediately re-attempting a tick that just failed/idled, and that
# recovers a chain-state left stuck `in_progress` by a hard-killed prior run.
GATE_OUTPUT=$(python3 "$CHAIN_STATE_SCRIPT" gate \
  --state-file "$CLAUDE_LOOP_CHAIN_STATE_FILE" \
  --max-stale-age-seconds "$CLAUDE_LOOP_MAX_STALE_AGE_SEC" 2>/root/vps-loop-chain-state.err)
ALLOWED="false"
REASON=""
RECOVERED="false"
eval "$GATE_OUTPUT"

if [[ "$RECOVERED" == "true" ]]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ): recovered a stale in-progress chain-state flag (a prior tick was killed before recording its outcome)"
fi
if [[ "$ALLOWED" != "true" ]]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ): skipping this invocation — $REASON"
  exit 0
fi

TICKS_RUN=0
FINAL_EXIT=0
CHAIN_START_SECONDS=$SECONDS

while (( TICKS_RUN < CLAUDE_LOOP_MAX_CHAIN_TICKS )); do
  ELAPSED_SECONDS=$(( SECONDS - CHAIN_START_SECONDS ))
  if (( ELAPSED_SECONDS >= CLAUDE_LOOP_MAX_CHAIN_WALLCLOCK_SEC )); then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ): chain wall-clock ceiling (${CLAUDE_LOOP_MAX_CHAIN_WALLCLOCK_SEC}s) reached after ${TICKS_RUN} tick(s); stopping this invocation's chain"
    break
  fi

  python3 "$CHAIN_STATE_SCRIPT" begin --state-file "$CLAUDE_LOOP_CHAIN_STATE_FILE" --pid $$ >/dev/null 2>>/root/vps-loop-chain-state.err

  timeout --foreground --kill-after=30s "${CLAUDE_TICK_TIMEOUT_SEC}s" \
    env CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 \
    claude --model "$CLAUDE_MODEL" --permission-mode auto -p "/vps-loop-run" --output-format text
  CLAUDE_EXIT=$?
  FINAL_EXIT=$CLAUDE_EXIT
  TICKS_RUN=$((TICKS_RUN + 1))

  # Fold vps-loop-run's own progress signal into the heartbeat: scripts/vps_loop_health.py
  # parses NEXT_TASK.md's Status log for last_successful_tick/current_item/last_tick_outcome/
  # blocker_class/paused state and the repeated_without_progress/stale_pause alert flags.
  # Pre-declare defaults before eval'ing its --format shell output so a parse error there
  # (it fails closed, non-zero exit, empty stdout) never leaves a variable unset under this
  # script's own `set -u`.
  VPS_LOOP_LAST_SUCCESSFUL_TICK=""
  VPS_LOOP_CURRENT_ITEM=""
  VPS_LOOP_LAST_TICK_OUTCOME=""
  VPS_LOOP_BLOCKER_CLASS=""
  VPS_LOOP_PAUSED="false"
  VPS_LOOP_PAUSED_SINCE=""
  VPS_LOOP_REPEATED_WITHOUT_PROGRESS="false"
  VPS_LOOP_STALE_PAUSE="false"
  HEALTH_SHELL_OUTPUT=$(python3 scripts/vps_loop_health.py --repo /root/transit-app \
    --out /root/vps-loop-health.json --format shell 2>/root/vps-loop-health.err) || true
  eval "$HEALTH_SHELL_OUTPUT"

  # A non-zero `claude` exit (the tick's own hard timeout, a crash) overrides
  # whatever the Status log happens to say -- that log entry may predate the
  # failure entirely, or may not exist yet this tick.
  CHAIN_OUTCOME="$VPS_LOOP_LAST_TICK_OUTCOME"
  if [[ "$CLAUDE_EXIT" -ne 0 || -z "$CHAIN_OUTCOME" ]]; then
    CHAIN_OUTCOME="unknown"
  fi

  RECORD_OUTPUT=$(python3 "$CHAIN_STATE_SCRIPT" record-outcome \
    --state-file "$CLAUDE_LOOP_CHAIN_STATE_FILE" \
    --outcome "$CHAIN_OUTCOME" \
    --backoff-base-seconds "$CLAUDE_LOOP_BACKOFF_BASE_SEC" \
    --backoff-cap-seconds "$CLAUDE_LOOP_BACKOFF_CAP_SEC" 2>>/root/vps-loop-chain-state.err)
  ACTION="stop"
  CHAIN_NEXT_EARLIEST_ATTEMPT=""
  CHAIN_CONSECUTIVE_NON_PROGRESS=""
  eval "$RECORD_OUTPUT"

  gh api repos/TripAndCode/transit-app/dispatches \
    -f event_type=vps-heartbeat \
    -F "client_payload[paused]=$VPS_LOOP_PAUSED" \
    -F "client_payload[repeated_without_progress]=$VPS_LOOP_REPEATED_WITHOUT_PROGRESS" \
    -F "client_payload[stale_pause]=$VPS_LOOP_STALE_PAUSE" \
    -F "client_payload[blocker_class]=$VPS_LOOP_BLOCKER_CLASS" \
    -F "client_payload[current_item]=$VPS_LOOP_CURRENT_ITEM" \
    -F "client_payload[last_successful_tick]=$VPS_LOOP_LAST_SUCCESSFUL_TICK" \
    -F "client_payload[paused_since]=$VPS_LOOP_PAUSED_SINCE" \
    -F "client_payload[chain_tick_outcome]=$CHAIN_OUTCOME" \
    -F "client_payload[chain_ticks_run]=$TICKS_RUN" \
    -F "client_payload[chain_consecutive_non_progress]=$CHAIN_CONSECUTIVE_NON_PROGRESS" \
    -F "client_payload[chain_next_earliest_attempt]=$CHAIN_NEXT_EARLIEST_ATTEMPT" \
    >/dev/null 2>&1

  if [[ "$ACTION" != "continue" ]]; then
    break
  fi
  sleep "$CLAUDE_LOOP_CHAIN_DELAY_SEC"
done

exit "$FINAL_EXIT"
