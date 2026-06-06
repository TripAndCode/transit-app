# Collector v3 migration runbook (supersedes CUTOVER.md — do not run both)

Spec: docs/superpowers/specs/2026-06-06-collector-v3-design.md
Aomori RT gap: < 1 minute (step 4). Old tree left intact 1 week as fallback.

## Important behavior change: Aomori static GTFS
Agency 1 (Aomori) has NO direct static zip URL — its `static_url` is an
opendata index page consumed by the `aomori_index_scrape` strategy in the
Python pipeline. The v3 VM collector therefore does NOT fetch Aomori static
(empty `static_url` column in `agencies.tsv`). After cutover, Aomori static is
collected workstation-side by `gtfs_pipeline.py refresh-static` /
`make fetch-ingest`, which scrape the source directly. Hiroshima (8/9/10)
static IS collected on the VM via `direct_url` curl.

## 0. Prereqs
- [ ] Create 4 checks at https://healthchecks.io (period 10 min, grace 5 min); note ping URLs.
- [ ] `feat/collector-v3` merged; `oracle_cloud/v3/` present on workstation.

## 1. Install tree (no impact on running v1)
    # from workstation repo root:
    KEY=oracle_cloud/ssh-key-2026-03-28.key; VM=opc@64.110.114.101
    ssh -i $KEY $VM 'mkdir -p /home/opc/collector/{etc,bin,data}'
    scp -i $KEY oracle_cloud/v3/bin/* $VM:/home/opc/collector/bin/
    scp -i $KEY oracle_cloud/v3/etc/agencies.tsv.example $VM:/home/opc/collector/etc/agencies.tsv
    scp -i $KEY oracle_cloud/v3/rt-poller@.service oracle_cloud/v3/logrotate-collector.conf oracle_cloud/v3/crontab.snippet $VM:/home/opc/
    ssh -i $KEY $VM 'chmod +x /home/opc/collector/bin/*.sh'
    # on VM: edit etc/agencies.tsv — replace FILL-ME ping URLs; verify TABs survived:
    ssh -i $KEY $VM "awk -F'\t' '!/^#/ && NF!=6 {print \"BAD ROW: \" \$0}' /home/opc/collector/etc/agencies.tsv"

## 2. Move existing data (instant, same filesystem)
    ssh -i $KEY $VM
    cd /home/opc/app/transportation_analysis
    mkdir -p /home/opc/collector/data/1/{rt,static}
    mv archive/2026*.tar.gz /home/opc/collector/data/1/rt/
    mv static_archive/gtfs_static_*.zip /home/opc/collector/data/1/static/
    cd /home/opc/collector/data/1/static && ln -sfn "$(ls gtfs_static_*.zip | sort | tail -1)" latest.zip

## 3. Install systemd unit + logrotate (sudo, on VM)
    sudo cp /home/opc/rt-poller@.service /etc/systemd/system/
    sudo cp /home/opc/logrotate-collector.conf /etc/logrotate.d/collector
    sudo systemctl daemon-reload

## 4. Cutover Aomori (the <1 min gap)
    OLD_PID=$(pgrep -f 'transportation_analysis/poller.sh' | head -1)
    [ -n "$OLD_PID" ] && kill "$OLD_PID"
    today=$(date -u +%Y%m%d)
    [ -d "/home/opc/app/transportation_analysis/archive/$today" ] && \
        mv "/home/opc/app/transportation_analysis/archive/$today" /home/opc/collector/data/1/rt/
    sudo systemctl enable --now rt-poller@1
    journalctl -u rt-poller@1 -n 5   # expect OK lines

## 5. Replace crontab (on VM)
    crontab -l > /tmp/crontab.backup.$(date +%s)
    crontab /home/opc/crontab.snippet   # prune line stays commented

## 6. Start Hiroshima
    sudo systemctl enable --now rt-poller@8 rt-poller@9 rt-poller@10
    journalctl -u rt-poller@8 -n 5

## 7. Workstation: v3 fetch + parity gate
    # .env: add COLLECTOR_DATA_DIR=/home/opc/collector/data
    # FIRST move the pre-existing local flat mirror into per-agency dirs:
    mkdir -p raw_archives/1 && mv raw_archives/*.tar.gz raw_archives/1/ 2>/dev/null || true
    mkdir -p raw_archives_static/1 && mv raw_archives_static/*.zip raw_archives_static/1/ 2>/dev/null || true
    make fetch
    # parity check (counts must match):
    ssh -i $KEY $VM 'ls /home/opc/collector/data/1/rt/*.tar.gz | wc -l'
    ls raw_archives/1/*.tar.gz | wc -l

## 8. Verify cron fired (next morning JST, on VM)
    tail -20 /home/opc/collector/cron.log     # expect static-fetch + rotate lines
    ls /home/opc/collector/data/1/rt/         # yesterday tarred, today live dir
    systemctl status 'rt-poller@*' --no-pager # all active

## 9. Enable prune (ONLY after step 7 parity + step 8 verification)
    crontab -e   # uncomment the prune line

## 10. +1 week: remove old tree
    rm -rf /home/opc/app/transportation_analysis/{poller.sh,poller_static.sh,cron.log,poller.log,static_poller.log,static_cron.log,archive,static_archive}

## Rollback (any point before step 10)
    sudo systemctl disable --now 'rt-poller@1' 'rt-poller@8' 'rt-poller@9' 'rt-poller@10'
    crontab /tmp/crontab.backup.*    # restores @reboot v1 line
    mv /home/opc/collector/data/1/rt/*.tar.gz /home/opc/app/transportation_analysis/archive/ 2>/dev/null
    nohup nice -n 10 /home/opc/app/transportation_analysis/poller.sh >> /home/opc/app/transportation_analysis/cron.log 2>&1 & disown
