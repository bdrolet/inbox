# ntfy Setup

Push notifications for urgent inbox messages (Phase 4). Self-hosted on a GCP e2-micro VM at `ntfy.drolet.ai`.

## Deployed state

| | |
|---|---|
| **URL** | `https://ntfy.drolet.ai` |
| **VM** | `ntfy`, e2-micro, us-central1-a |
| **Static IP** | `34.67.20.72` |
| **TLS** | Let's Encrypt, expires 2026-09-02, auto-renews via cron |
| **Auth** | `auth-default-access: deny-all`; admin user `ben` |
| **iOS delivery** | `upstream-base-url: https://ntfy.sh` (APNs relay) |
| **Access token** | Secret Manager: `ntfy-token` |
| **User password** | Secret Manager: `ntfy-password` |

---

## Mobile app setup

1. Install the ntfy app (iOS or Android)
2. Tap **+** → enter topic name → tap **Use another server** → `https://ntfy.drolet.ai`
3. Expand credentials → **Username:** `ben` | **Password:** from Secret Manager:
   ```bash
   gcloud secrets versions access latest --secret=ntfy-password --project=bens-project-462804 | pbcopy
   ```

---

## Infrastructure

### GCP (`terraform/ntfy.tf`)

- `google_compute_address.ntfy` — static IP
- `google_compute_firewall.ntfy_http` + `ntfy_https` — ports 80/443 on tag `ntfy-server`
- `google_compute_instance.ntfy` — e2-micro, debian-12, startup script installs ntfy + certbot

### DNS (`~/src/infra/cloudflare/ntfy.tf`)

`cloudflare_record.ntfy` — A record, `ntfy.drolet.ai → 34.67.20.72`, DNS-only (`proxied = false`).

### server.yml (current)

```yaml
base-url: https://ntfy.drolet.ai
listen-https: :443
cert-file: /etc/letsencrypt/live/ntfy.drolet.ai/fullchain.pem
key-file: /etc/letsencrypt/live/ntfy.drolet.ai/privkey.pem
cache-file: /var/cache/ntfy/cache.db
auth-file: /var/lib/ntfy/user.db
auth-default-access: deny-all
upstream-base-url: https://ntfy.sh
```

---

## Skills

- `/deploy-ntfy` — provision, update, or bootstrap TLS on the VM
- `/ntfy-logs` — fetch logs via `gcloud compute ssh`

---

## Inbox integration

`clients/ntfy.py` POSTs to `{NTFY_BASE_URL}/{NTFY_TOPIC}` with a Bearer token. The processor CF receives `NTFY_BASE_URL`, `NTFY_TOPIC`, and `NTFY_TOKEN` as env vars (the token from Secret Manager).

Set `ntfy_topic` in `terraform/terraform.tfvars` then run `/deploy-inbox` to activate notifications.

### Test a notification

```bash
curl \
  -H "Authorization: Bearer $(gcloud secrets versions access latest --secret=ntfy-token --project=bens-project-462804)" \
  -H "Title: Test" \
  -d "test notification" \
  https://ntfy.drolet.ai/inbox
```

---

## VM operations

### SSH in

```bash
gcloud compute ssh ntfy --project bens-project-462804 --zone us-central1-a
```

### Edit config

```bash
gcloud compute ssh ntfy --project bens-project-462804 --zone us-central1-a \
  --command "sudo nano /etc/ntfy/server.yml && sudo systemctl restart ntfy"
```

### Upgrade ntfy

```bash
gcloud compute ssh ntfy --project bens-project-462804 --zone us-central1-a \
  --command "
NTFY_VERSION=\$(curl -s https://api.github.com/repos/binwiederhier/ntfy/releases/latest | grep '\"tag_name\"' | cut -d'\"' -f4 | tr -d v)
curl -sLo /tmp/ntfy.deb \"https://github.com/binwiederhier/ntfy/releases/download/v\$NTFY_VERSION/ntfy_\${NTFY_VERSION}_linux_amd64.deb\"
sudo dpkg -i /tmp/ntfy.deb && sudo systemctl restart ntfy
"
```

### Check cert status

```bash
gcloud compute ssh ntfy --project bens-project-462804 --zone us-central1-a \
  --command "sudo certbot certificates"
```

---

## Notes

- `packages.ntfy.sh` apt repo is NXDOMAIN — install ntfy from GitHub releases only
- ntfy runs as the `ntfy` user; `/etc/letsencrypt/` permissions must be set after certbot runs (handled by `bootstrap-tls.sh`)
- `proxied = false` on the Cloudflare record is required for Let's Encrypt HTTP-01 challenge
- iOS notifications require `upstream-base-url: https://ntfy.sh` — without it, `subscribers=0` and messages are cached but never delivered to the phone
