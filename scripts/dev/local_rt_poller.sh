#!/usr/bin/env bash
# Keeps local dev's GTFS-RT data fresh by re-running the existing
# `ingest_live` fetch-and-ingest pass on a fixed interval. `ingest_live`
# already fetches every configured agency's feed_url and ingests in one
# call — the same logic the app's own manual refresh endpoint uses — so
# this loop adds no new ingest behavior, only repetition.
#
# Wired into `make serve` (see Makefile); not part of the production path,
# which streams from the Oracle collector or the daily Railway ingest job
# instead (see docs/deploy-railway.md).
set -uo pipefail

trap 'exit 0' TERM INT

while true; do
    poetry run python gtfs_pipeline.py ingest_live
    sleep "${LOCAL_RT_POLL_INTERVAL_SEC:-30}"
done
