#!/usr/bin/env bash
# Shared test helpers: temp COLLECTOR_BASE + a PATH-shimmed fake curl.
set -euo pipefail

setup_base() {
    TEST_BASE=$(mktemp -d)
    export COLLECTOR_BASE="$TEST_BASE"
    mkdir -p "$TEST_BASE/etc" "$TEST_BASE/bin"
    SHIM_DIR=$(mktemp -d)
    export PATH="$SHIM_DIR:$PATH"
    export CURL_LOG="$TEST_BASE/curl.log"
    # Fake curl: records args; honors `--output FILE` by writing canned bytes.
    # CURL_FAIL=1 simulates network failure (exit 22 like curl -f).
    cat > "$SHIM_DIR/curl" <<'SHIM'
#!/usr/bin/env bash
echo "$@" >> "$CURL_LOG"
[ "${CURL_FAIL:-0}" = "1" ] && exit 22
out=""
prev=""
for a in "$@"; do
    [ "$prev" = "--output" ] && out="$a"
    prev="$a"
done
if [ -n "$out" ]; then
    printf 'PBDATA-%s' "${CURL_BODY:-default}" > "$out"
fi
exit 0
SHIM
    chmod +x "$SHIM_DIR/curl"
}

teardown_base() {
    rm -rf "$TEST_BASE" "$SHIM_DIR"
}

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
