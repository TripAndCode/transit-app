#!/usr/bin/env bash
# Mandatory local first line of defense for secret scanning: installs the
# pinned `gitleaks` binary (if missing) and the pre-commit hook that runs it
# on every commit (see .pre-commit-config.yaml). Called by `make bootstrap`
# and `make hooks`; both fail the whole target if this script fails, since a
# workstation or VPS clone that silently skipped hook install would have no
# local secret-scanning gate at all -- CI's secrets-scan.yml is the only
# remaining backstop, and it only runs on pushes CI actually processes (this
# repo's [skip ci] convention means most local commits never reach it).
#
# `git worktree`s share one .git/hooks directory (it lives in the common git
# dir, not per-worktree), so a single run of this script against any
# worktree of a clone installs the hook for every worktree of that same
# clone -- one run on the VPS's persistent checkout covers every
# /vps-loop-run worker worktree cut from it.
#
# Usage: scripts/setup_git_hooks.sh
#        scripts/setup_git_hooks.sh --check   # verify only, install nothing
# Exits nonzero with an actionable message on any step it cannot complete.
# --check runs only the "is the hook really installed" verification (exec
# bit + pre-commit marker + no core.hooksPath override) and exits 0/1 with
# no side effects -- the single source of truth for `make doctor` and
# deploy/vps/claude-loop.sh's per-tick check, so they can't drift out of
# sync with what install_hook itself considers "installed".
set -euo pipefail

# Pinned to match .pre-commit-config.yaml's `rev: v8.18.4` and
# .github/workflows/secrets-scan.yml's GITLEAKS_VERSION so local, hook, and
# CI scans always agree on which ruleset/binary version ran.
# tests/unit/test_gitleaks_version_pin.py asserts these three stay in sync.
GITLEAKS_VERSION="8.18.4"
GITLEAKS_INSTALL_DIR="${GITLEAKS_INSTALL_DIR:-$HOME/.local/bin}"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

fail() {
  echo "setup_git_hooks: $1" >&2
  exit 1
}

detect_platform() {
  local os arch
  case "$(uname -s)" in
    Linux) os="linux" ;;
    Darwin) os="darwin" ;;
    *) fail "unsupported OS $(uname -s) for automatic gitleaks install -- install gitleaks ${GITLEAKS_VERSION} manually and re-run this script" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch="x64" ;;
    arm64|aarch64) arch="arm64" ;;
    *) fail "unsupported architecture $(uname -m) for automatic gitleaks install -- install gitleaks ${GITLEAKS_VERSION} manually and re-run this script" ;;
  esac
  printf '%s_%s' "$os" "$arch"
}

install_gitleaks() {
  if command -v gitleaks >/dev/null 2>&1; then
    local installed_version
    installed_version="$(gitleaks version 2>/dev/null | tr -d '[:space:]')"
    if [ "$installed_version" = "$GITLEAKS_VERSION" ]; then
      echo "-> gitleaks ${GITLEAKS_VERSION} already installed"
      return 0
    fi
    echo "-> gitleaks found (version ${installed_version:-unknown}) but pinned version is ${GITLEAKS_VERSION}; installing the pinned build to ${GITLEAKS_INSTALL_DIR}"
  else
    echo "-> gitleaks not found; installing pinned ${GITLEAKS_VERSION} to ${GITLEAKS_INSTALL_DIR}"
  fi

  command -v curl >/dev/null 2>&1 || fail "curl is required to install gitleaks automatically -- install curl, or install gitleaks ${GITLEAKS_VERSION} manually from https://github.com/gitleaks/gitleaks/releases and re-run"
  command -v tar >/dev/null 2>&1 || fail "tar is required to install gitleaks automatically -- install tar, or install gitleaks ${GITLEAKS_VERSION} manually and re-run"

  local platform archive_url tmp_archive
  platform="$(detect_platform)"
  archive_url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${platform}.tar.gz"
  tmp_archive="$(mktemp -t gitleaks-XXXXXX.tar.gz)"

  if ! curl -sSfL "$archive_url" -o "$tmp_archive"; then
    fail "could not download gitleaks ${GITLEAKS_VERSION} from ${archive_url} (no network, or the pinned release was removed) -- install it manually and re-run"
  fi

  mkdir -p "$GITLEAKS_INSTALL_DIR"
  if ! tar -xzf "$tmp_archive" -C "$GITLEAKS_INSTALL_DIR" gitleaks; then
    fail "downloaded archive did not contain a gitleaks binary at the expected layout -- install gitleaks ${GITLEAKS_VERSION} manually and re-run"
  fi
  chmod +x "$GITLEAKS_INSTALL_DIR/gitleaks"

  local final_version
  final_version="$("$GITLEAKS_INSTALL_DIR/gitleaks" version 2>/dev/null | tr -d '[:space:]')"
  [ "$final_version" = "$GITLEAKS_VERSION" ] || fail "installed gitleaks reports version '${final_version}', expected ${GITLEAKS_VERSION} -- installation is not trustworthy, refusing to continue"

  # The pre-commit hook invokes unqualified `gitleaks` (language: system),
  # resolved via PATH at commit time -- not this absolute install path. If
  # another gitleaks (e.g. from `brew install gitleaks`) sits earlier on
  # PATH than GITLEAKS_INSTALL_DIR, the check above would pass while the
  # hook silently keeps running the wrong version. Verify what unqualified
  # `gitleaks` actually resolves to as well.
  if ! command -v gitleaks >/dev/null 2>&1; then
    echo "-> NOTE: ${GITLEAKS_INSTALL_DIR} is not on PATH; add it to your shell profile so 'gitleaks' resolves outside this script"
  else
    local resolved_path resolved_version
    resolved_path="$(command -v gitleaks)"
    resolved_version="$(gitleaks version 2>/dev/null | tr -d '[:space:]')"
    if [ "$resolved_version" != "$GITLEAKS_VERSION" ]; then
      fail "a different gitleaks resolves earlier on PATH at ${resolved_path} (version ${resolved_version:-unknown}), not the pinned ${GITLEAKS_VERSION} just installed to ${GITLEAKS_INSTALL_DIR}/gitleaks -- the pre-commit hook invokes unqualified 'gitleaks' and would silently run the wrong version; remove ${resolved_path} or reorder PATH so ${GITLEAKS_INSTALL_DIR} comes first, then re-run"
    fi
  fi

  echo "-> installed gitleaks ${GITLEAKS_VERSION} to ${GITLEAKS_INSTALL_DIR}/gitleaks"
}

install_pre_commit() {
  if command -v pre-commit >/dev/null 2>&1; then
    echo "-> pre-commit already installed"
    return 0
  fi
  echo "-> pre-commit not found; attempting install"
  # Deliberately not a poetry/project dependency: pre-commit must run
  # standalone from the git hook (invoked directly by git, outside any
  # `poetry run`), so it needs its own interpreter's package to be
  # importable at hook-run time -- installing it into this project's
  # poetry-managed virtualenv would only make it resolvable via
  # `poetry run pre-commit`, not from the hook script git itself invokes.
  if command -v pipx >/dev/null 2>&1; then
    pipx install pre-commit || fail "'pipx install pre-commit' failed -- install pre-commit manually (https://pre-commit.com/#installation) and re-run"
  elif command -v pip3 >/dev/null 2>&1; then
    pip3 install --user pre-commit || fail "'pip3 install --user pre-commit' failed -- install pre-commit manually (https://pre-commit.com/#installation) and re-run"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --user pre-commit || fail "'python3 -m pip install --user pre-commit' failed -- install pre-commit manually (https://pre-commit.com/#installation) and re-run"
  else
    fail "no supported installer (pipx/pip3/python3 -m pip) found to install pre-commit -- install it manually (https://pre-commit.com/#installation) and re-run"
  fi
  command -v pre-commit >/dev/null 2>&1 || fail "pre-commit install command succeeded but 'pre-commit' is still not on PATH -- its installer's bin directory (e.g. ~/.local/bin) likely isn't on PATH; add it and re-run"
}

hook_file_path() {
  printf '%s/hooks/pre-commit' "$(git rev-parse --git-common-dir)"
}

# Single source of truth for "is the hook really installed": executable,
# carries pre-commit's own generated marker, and not shadowed by a
# core.hooksPath override. Echoes an empty string when the hook is fully
# installed, otherwise echoes an actionable reason it isn't. Used both by
# install_hook (which turns a non-empty reason into a hard `fail`) and by
# `--check` mode (Makefile's `doctor` target, deploy/vps/claude-loop.sh),
# so all three call sites can never drift out of sync on what "installed"
# means.
hook_verify_reason() {
  local hook_file
  hook_file="$(hook_file_path)"

  if [ ! -f "$hook_file" ]; then
    echo "expected pre-commit to write ${hook_file}, but it does not exist -- hook install did not take effect, commits are NOT protected"
    return
  fi
  if [ ! -x "$hook_file" ]; then
    echo "${hook_file} exists but is not executable -- commits would silently skip the hook, refusing to report success"
    return
  fi
  if ! grep -q "File generated by pre-commit" "$hook_file"; then
    echo "${hook_file} exists but does not look like a pre-commit-managed hook (missing its generated marker) -- a stale or hand-written hook may be shadowing gitleaks; remove it and re-run"
    return
  fi

  local hooks_path
  hooks_path="$(git config --get core.hooksPath || true)"
  if [ -n "$hooks_path" ]; then
    echo "git config core.hooksPath is set to '${hooks_path}', which overrides the standard hooks directory pre-commit just wrote to -- commits would bypass the gitleaks hook entirely. Unset it ('git config --unset core.hooksPath') and re-run"
    return
  fi
}

install_hook() {
  pre-commit install --hook-type pre-commit || fail "'pre-commit install' failed"

  local reason
  reason="$(hook_verify_reason)"
  [ -z "$reason" ] || fail "$reason"

  echo "-> gitleaks pre-commit hook installed at $(hook_file_path)"
}

if [ "${1:-}" = "--check" ]; then
  reason="$(hook_verify_reason)"
  if [ -z "$reason" ]; then
    exit 0
  fi
  echo "setup_git_hooks --check: hook not installed -- $reason" >&2
  exit 1
fi

install_gitleaks
install_pre_commit
install_hook
echo "setup_git_hooks: done -- commits in this clone are now scanned by gitleaks before they're created."
