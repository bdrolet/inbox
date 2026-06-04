---
name: deploy-ntfy
description: Use when the user wants to provision, update, or bootstrap the self-hosted ntfy VM on GCP; set up TLS; or update the ntfy config after changing terraform/ntfy.tf.
metadata:
  depends-on:
    - terraform-apply
    - using-1password-cli
---

# Deploying the ntfy Server

Self-hosted ntfy runs on a GCP e2-micro VM (`ntfy`, us-central1-a). Provisioning uses two Terraform workspaces: `inbox/terraform/` (GCP VM + firewall) and `~/src/infra/cloudflare/` (DNS A record).

**VM:** `ntfy` | **Zone:** `us-central1-a` | **Domain:** `ntfy.drolet.ai`

---

## Step 1 — Provision GCP resources

Use the **terraform-apply** skill to apply the inbox workspace. This creates:
- Static IP (`google_compute_address.ntfy`)
- Firewall rules for ports 80 and 443 (`ntfy-server` tag)
- e2-micro VM with ntfy + certbot installed via startup script

After apply, capture the static IP:
```bash
terraform -chdir=/Users/ben/src/inbox/terraform output -raw ntfy_ip
```

---

## Step 2 — Create DNS record

The Cloudflare workspace is at `~/src/infra/cloudflare/`. It needs the static IP from Step 1 and the Cloudflare API token from 1Password.

Use the **using-1password-cli** skill to get the Cloudflare API token, then:
```bash
cd ~/src/infra/cloudflare
terraform init  # if not already initialised
terraform apply -var "ntfy_ip=<IP_FROM_STEP_1>"
```

This creates `ntfy.drolet.ai A <IP>` (DNS-only, proxied=false — required for Let's Encrypt to work).

---

## Step 3 — Bootstrap TLS (first time only)

Wait ~30 seconds for DNS to propagate, then SSH into the VM and run the bootstrap script:
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo /root/bootstrap-tls.sh"
```

This stops ntfy, runs `certbot --standalone` on port 80, fixes cert permissions (ntfy runs as the `ntfy` user which needs read access to `/etc/letsencrypt/`), reconfigures ntfy to HTTPS on port 443, and restarts it. Auto-renewal is added to cron.

---

## Step 4 — Verify

```bash
curl -s https://ntfy.drolet.ai/v1/health
# Expected: {"healthy":true}
```

Send a test notification:
```bash
curl -d "test from deploy" https://ntfy.drolet.ai/<your-topic>
```

---

## Step 5 — Wire up the processor CF

Set `ntfy_topic` in `terraform/terraform.tfvars` (pick a hard-to-guess name, e.g. `inbox-alerts-xk9m2p` — treat it like a password), then re-apply the inbox workspace to update the `NTFY_TOPIC` env var on `inbox-process`.

Subscribe on mobile: open the ntfy app → **+** → enter topic name → server URL `https://ntfy.drolet.ai`.

---

## Updating ntfy config

For config-only changes (`/etc/ntfy/server.yml`), SSH in and edit directly:
```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo nano /etc/ntfy/server.yml && sudo systemctl restart ntfy"
```

For VM or firewall changes, re-apply inbox terraform via the **terraform-apply** skill.

---

## Upgrading ntfy

```bash
gcloud compute ssh ntfy \
  --project bens-project-462804 \
  --zone us-central1-a \
  --command "sudo apt-get update && sudo apt-get install -y ntfy && sudo systemctl restart ntfy"
```
