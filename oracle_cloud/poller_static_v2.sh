#!/usr/bin/env bash
# Daily static GTFS refresh: delegates to gtfs_pipeline.py refresh-static which
# iterates every agency with a configured static_strategy.
set -euo pipefail

BASE_DIR="/home/opc/app/transportation_analysis"
LOG_FILE="$BASE_DIR/static_poller.log"
REPO_DIR="$BASE_DIR/transit-app"   # adjust to actual checkout path

cd "$REPO_DIR"
{
    echo "[$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S %Z')] static refresh start"
    poetry run python gtfs_pipeline.py refresh-static --dest "$BASE_DIR/static_archive"
    echo "[$(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S %Z')] static refresh end"
} >> "$LOG_FILE" 2>&1
