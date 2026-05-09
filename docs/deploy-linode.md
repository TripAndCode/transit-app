# Deploy to Linode (Tokyo)

Single 2 GB Linode running `docker compose --profile prod`. App + Caddy + PostGIS/pgvector all on one box. Designed for portfolio traffic. ~$12/mo.

---

## 1. Provision the Linode

1. [Linode → Create Linode](https://cloud.linode.com/linodes/create)
2. **Image:** Ubuntu 24.04 LTS
3. **Region:** Tokyo (`ap-northeast-1`) or Osaka (`jp-osa`)
4. **Plan:** Shared CPU → **Linode 2 GB** ($12/mo)
5. Add your SSH public key
6. Set a root password (you won't use it, but Linode requires one)
7. Boot. Note the public IPv4.

```bash
# from your Mac
ssh root@<LINODE_IP>
```

## 2. Harden the box

```bash
# Create deploy user
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

# Firewall: allow SSH + HTTP(S) only. Postgres stays internal.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Disable root SSH + password auth
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh

# Re-login as deploy
exit
ssh deploy@<LINODE_IP>
```

## 3. Install Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
# log out + back in so docker group takes effect
exit
ssh deploy@<LINODE_IP>
docker run --rm hello-world  # sanity check
```

## 4. Clone + configure

```bash
git clone https://github.com/<you>/transit-app.git
cd transit-app

cp .env.example .env
nano .env
```

Set in `.env`:

```
GROQ_API_KEY=gsk_...
CRON_SECRET=$(openssl rand -hex 32)         # paste output, then save same value as GH secret
POSTGRES_PASSWORD=$(openssl rand -hex 24)   # paste output
CADDY_SITE_ADDRESS=:80                       # IP-only first boot. Swap to domain in step 7.
CORS_ORIGINS=                                 # leave empty: app + SPA same-origin behind Caddy
```

Optional: drop the Oracle vars block — VPS deploy ingests via the cron endpoint, not rsync.

## 5. First boot

```bash
docker compose --profile prod up -d --build
docker compose ps                     # all 3 services healthy?
docker compose logs -f app caddy      # ctrl-c when stable
```

Smoke test:

```bash
curl -fsS http://<LINODE_IP>/health   # → 200, app reachable through Caddy
```

Open `http://<LINODE_IP>` in a browser — SPA loads. Browser will warn about HTTP; that's expected until step 7.

## 6. Wire GitHub Actions cron to the new URL

```bash
# from your Mac
gh secret set APP_BASE_URL --body "http://<LINODE_IP>"
gh secret set CRON_SECRET  --body "<the same value you put in .env>"

# trigger once manually to verify
gh workflow run "Hourly Ingest"
gh run watch
```

`docker compose logs -f app` on the box should show the ingest hit.

## 7. Domain + HTTPS (when ready)

1. Buy a domain (suggestions in `README.md`'s Deploy section).
2. DNS A record: `@` → `<LINODE_IP>` (also `www` if you want it).
3. Wait for DNS to propagate (`dig +short transit-delay.app` returns the IP).
4. On the box:
   ```bash
   sed -i 's/^CADDY_SITE_ADDRESS=.*/CADDY_SITE_ADDRESS=transit-delay.app/' .env
   docker compose --profile prod up -d
   docker compose logs -f caddy   # watch Let's Encrypt issue
   ```
5. Update GH secret: `gh secret set APP_BASE_URL --body "https://transit-delay.app"`.

Caddy auto-renews. Nothing else to do.

## 8. Backups

Cheap option for portfolio: nightly `pg_dump` to Linode Object Storage ($5/mo, S3-compatible) or just keep weekly snapshots via Linode's built-in Backup add-on (~$2.50/mo for 2 GB plan).

Skip entirely if you really only have demo data.

```bash
# Cron on the host, NOT in the container
crontab -e
# 0 17 * * *  cd /home/deploy/transit-app && docker compose exec -T db pg_dump -U transit transit | gzip > /home/deploy/backups/transit-$(date +\%F).sql.gz
```

## 9. Updates

```bash
ssh deploy@<LINODE_IP>
cd transit-app
git pull
docker compose --profile prod up -d --build   # rebuilds app, leaves db running
```

`release_command` (Fly's migration step) doesn't apply here — bake migrations into the app container's startup or run manually:

```bash
docker compose exec app python gtfs_pipeline.py migrate up
```

(Or add a one-shot `migrate` service in compose; not strictly needed for portfolio.)

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Caddy stuck "obtaining cert" | DNS A record points to Linode IP? Port 80 reachable from outside (`ufw status`)? |
| `app` healthcheck failing | `docker compose logs app` — usually `DATABASE_URL` typo or `GROQ_API_KEY` missing. |
| Cron returns 401 | `CRON_SECRET` mismatch between `.env` and GH secret. |
| Out of memory | `docker stats` — Postgres is the usual hog. Bump to Linode 4 GB ($24/mo) or add swap (`fallocate -l 2G /swapfile`). |
