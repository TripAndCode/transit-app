#!/usr/bin/env bash
# deploy/vps/bootstrap.sh — one-time, idempotent setup of the ML workbench.
# Run as root from /root/transit-app after /etc/transit-ml/env exists.
set -euo pipefail
cd /root/transit-app
test -f /etc/transit-ml/env || { echo "missing /etc/transit-ml/env (see deploy/vps/env.example)" >&2; exit 1; }
set -a
. /etc/transit-ml/env
set +a

command -v aws >/dev/null || snap install aws-cli --classic
docker compose -f deploy/vps/compose.yml --env-file /etc/transit-ml/env up -d --build --wait

# The environment variable rather than `poetry config --local`, which would
# leave an untracked poetry.toml in the clone.
POETRY_VIRTUALENVS_IN_PROJECT=true "${POETRY:-$HOME/.local/bin/poetry}" install --only main --no-interaction
.venv/bin/python gtfs_pipeline.py migrate up
.venv/bin/python -c "from pipeline.clickhouse import get_client; from db.clickhouse.bootstrap import apply_schema; apply_schema(get_client())"
.venv/bin/python gtfs_pipeline.py seed_agencies agencies.csv

mkdir -p /var/lib/transit-ml
install -m 644 deploy/vps/systemd/*.service deploy/vps/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
for timer in deploy/vps/systemd/*.timer; do systemctl enable --now "$(basename "$timer")"; done
