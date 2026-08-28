#!/bin/sh
# Strip the Vite build-manifest directory (dist/.vite/manifest.json, from
# vite.config.ts's build.manifest: true, used by
# frontend/scripts/check-entry-chunk.mjs) out of a built frontend tree
# before it ships as production static assets. It's a build-time-only
# artifact — api/main.py's SPA fallback would otherwise serve it publicly.
#
# Shared by two independent build paths so they can't drift out of sync:
#   - Dockerfile: stripped from the frontend stage's dist/ before
#     COPY --from=frontend copies it into the image.
#   - Makefile's `bake` target: stripped from api/static/ after copying
#     frontend/dist there for local single-origin `make serve`.
#
# Usage: strip_vite_manifest.sh <dir-containing-a-.vite-subdir>
set -eu

if [ -z "${1:-}" ]; then
  echo "usage: $0 <dir-containing-a-.vite-subdir>" >&2
  exit 1
fi

rm -rf "$1/.vite"
