# Hiroshima cutover runbook (Oracle VM)

Performs the Phase 6 cutover: replace single-agency pollers with multi-agency
versions, migrate the existing Aomori archive layout under `archive/1/`, and
register agencies 8/9/10. Aomori RT may have a <1-minute gap during the swap;
that's within the existing ingest dedup envelope.

## Pre-cutover checklist (on dev workstation)

- [ ] All Phase 1–5 commits merged on `main`.
- [ ] `pytest -x` green locally.
- [ ] Hiroshima archive disk budget ≥ 50 MB/day projected (Hiroden largest at
  ~7 MB/day at current sizes).
- [ ] You can SSH to `opc@64.110.114.101`.

## Steps

```bash
# 1. SSH in
ssh -i oracle_cloud/ssh-key-2026-03-28.key opc@64.110.114.101

# 2. Pull latest repo on the VM
cd /home/opc/app/transportation_analysis
git -C transit-app pull   # or rsync if no git on the VM

# 3. Apply DB migration 0006
cd transit-app && poetry run python gtfs_pipeline.py migrate up && cd ..

# 4. Re-seed agencies (idempotent upsert; populates the new strategy columns)
cd transit-app && poetry run python gtfs_pipeline.py seed_agencies agencies.csv && cd ..

# 5. Export agencies.json for the v2 poller
cd transit-app && poetry run python -c '
import csv, json, sys
rows = []
for r in csv.DictReader(open("agencies.csv")):
    if r.get("feed_url"):
        rows.append({"agency_id": int(r["agency_id"]), "feed_url": r["feed_url"]})
json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
' > /home/opc/app/transportation_analysis/agencies.json && cd ..

# 6. Migrate existing Aomori archives into per-agency layout
cd /home/opc/app/transportation_analysis
mkdir -p archive/1
shopt -s nullglob
for x in archive/2026[01]*; do mv "$x" archive/1/; done

# 7. Stop the old poller
OLD_PID=$(ps -eo pid,cmd | grep "poller.sh$" | grep -v grep | awk '{print $1}')
[ -n "$OLD_PID" ] && kill "$OLD_PID"

# 8. Replace cron entries
crontab -l > /tmp/old_crontab
cat > /tmp/new_crontab <<'CRON'
@reboot nice -n 10 /home/opc/app/transportation_analysis/poller_v2.sh >> /home/opc/app/transportation_analysis/cron.log 2>&1
CRON_TZ=Asia/Tokyo
0 9 * * * /home/opc/app/transportation_analysis/poller_static_v2.sh
CRON
crontab /tmp/new_crontab

# 9. Install the v2 scripts
cp transit-app/oracle_cloud/poller_v2.sh \
   transit-app/oracle_cloud/poller_static_v2.sh \
   /home/opc/app/transportation_analysis/
chmod +x /home/opc/app/transportation_analysis/poller_*.sh

# 10. Start the v2 poller
nohup nice -n 10 /home/opc/app/transportation_analysis/poller_v2.sh \
  >> /home/opc/app/transportation_analysis/cron.log 2>&1 &
disown

# 11. Verify (within 60s)
ls -la /home/opc/app/transportation_analysis/archive/{1,8,9,10}/$(date -u +%Y%m%d)/ | tail -20
# expect new TripUpdate_*.pb files in all four
```

## Rollback

If something is wrong, restore the prior crontab and the old `poller.sh`:

```bash
crontab /tmp/old_crontab
nohup /home/opc/app/transportation_analysis/poller.sh \
  >> /home/opc/app/transportation_analysis/cron.log 2>&1 &
disown

# Then on the dev workstation:
cd transit-app && poetry run python gtfs_pipeline.py migrate down --target 0005
```

The down migration restores NOT NULL on `updates`. If any Hiroshima rows landed
with NULLs, the down migration will fail; fix by `DELETE FROM updates WHERE
service_type IS NULL OR scheduled_time IS NULL OR route_code IS NULL` first.
