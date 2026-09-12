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
# Exits nonzero with an actionable message on any step it cannot complete.
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

  case ":$PATH:" in
    *":$GITLEAKS_INSTALL_DIR:"*) ;;
    *) echo "-> NOTE: ${GITLEAKS_INSTALL_DIR} is not on PATH; add it to your shell profile so 'gitleaks' resolves outside this script" ;;
  esac

  local final_version
  final_version="$("$GITLEAKS_INSTALL_DIR/gitleaks" version 2>/dev/null | tr -d '[:space:]')"
  [ "$final_version" = "$GITLEAKS_VERSION" ] || fail "installed gitleaks reports version '${final_version}', expected ${GITLEAKS_VERSION} -- installation is not trustworthy, refusing to continue"
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

install_hook() {
  pre-commit install --hook-type pre-commit || fail "'pre-commit install' failed"

  local hooks_dir hook_file
  hooks_dir="$(git rev-parse --git-common-dir)/hooks"
  hook_file="${hooks_dir}/pre-commit"

  [ -f "$hook_file" ] || fail "expected pre-commit to write ${hook_file}, but it does not exist -- hook install did not take effect, commits are NOT protected"
  [ -x "$hook_file" ] || fail "${hook_file} exists but is not executable -- commits would silently skip the hook, refusing to report success"
  grep -q "File generated by pre-commit" "$hook_file" || fail "${hook_file} exists but does not look like a pre-commit-managed hook (missing its generated marker) -- a stale or hand-written hook may be shadowing gitleaks; remove it and re-run"

  local hooks_path
  hooks_path="$(git config --get core.hooksPath || true)"
  if [ -n "$hooks_path" ]; then
    fail "git config core.hooksPath is set to '${hooks_path}', which overrides the standard hooks directory pre-commit just wrote to -- commits would bypass the gitleaks hook entirely. Unset it ('git config --unset core.hooksPath') and re-run"
  fi

  echo "-> gitleaks pre-commit hook installed at ${hook_file}"
}

install_gitleaks
install_pre_commit
install_hook
echo "setup_git_hooks: done -- commits in this clone are now scanned by gitleaks before they're created."
