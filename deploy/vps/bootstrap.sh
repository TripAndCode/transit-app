#!/usr/bin/env bash
# deploy/vps/bootstrap.sh — one-time, idempotent setup of the ML workbench.
# Run as root from /root/transit-app after /etc/transit-ml/env exists.
set -euo pipefail
cd /root/transit-app
test -f /etc/transit-ml/env || { echo "missing /etc/transit-ml/env (see deploy/vps/env.example)" >&2; exit 1; }
set -a
. /etc/transit-ml/env
set +a

if ! command -v aws >/dev/null; then
  # AWS's own bundle rather than the snap: a server image need not run snapd.
  command -v unzip >/dev/null || { apt-get update -q && apt-get install -y -q unzip; }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "$tmp/awscli.zip"
  unzip -q "$tmp/awscli.zip" -d "$tmp"
  # With no `aws` on PATH, an install directory is a run cut short: start clean.
  rm -rf /usr/local/aws-cli
  "$tmp/aws/install"
  aws --version
fi
docker compose -f deploy/vps/compose.yml --env-file /etc/transit-ml/env up -d --build --wait

# The environment variable rather than `poetry config --local`, which would
# leave an untracked poetry.toml in the clone.
POETRY_VIRTUALENVS_IN_PROJECT=true "${POETRY:-$HOME/.local/bin/poetry}" install --only main,ml --no-interaction
.venv/bin/python gtfs_pipeline.py migrate up
.venv/bin/python -c "from pipeline.clickhouse import get_client; from db.clickhouse.bootstrap import apply_schema; apply_schema(get_client())"
.venv/bin/python gtfs_pipeline.py seed_agencies agencies.csv

mkdir -p /var/lib/transit-ml
install -m 644 deploy/vps/systemd/*.service deploy/vps/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
for timer in deploy/vps/systemd/*.timer; do systemctl enable --now "$(basename "$timer")"; done
