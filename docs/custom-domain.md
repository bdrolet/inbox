# Custom Domain: inbox-api.drolet.cloud

The `inbox-api` Cloud Run service is mapped to `inbox-api.drolet.cloud`.

## Setup status

- [x] `drolet.cloud` domain verified in Google Search Console (2026-06-24)
- [x] Cloud Run domain mapping created via `gcloud`
- [x] DNS record added in `infra` repo (`cloudflare/drolet-cloud.tf`) — CNAME `inbox-api` → `ghs.googlehosted.com`, DNS-only (not proxied)
- [x] TLS certificate provisioned; `https://inbox-api.drolet.cloud` serving (verified 2026-07-14)

All API skills (`searching-inbox-emails`, `fetching-inbox-email`, `sending-inbox-email`) point at the custom domain.

## Runbook (if the mapping ever needs recreating)

### 1. Verify domain ownership (one-time, already done)

```bash
gcloud domains verify drolet.cloud --project bens-project-462804
```

The TXT record `google-site-verification=u--cgn4dU1qJBQSpAJFEhX5RMqJWPjWInjgqDU4vVlw` is
in `cloudflare/drolet-cloud.tf` in the infra repo.

### 2. Create the domain mapping

```bash
gcloud beta run domain-mappings create \
  --service inbox-api \
  --domain inbox-api.drolet.cloud \
  --region us-central1 \
  --project bens-project-462804
```

This outputs the DNS records to create. For a subdomain, a CNAME to
`ghs.googlehosted.com` works; keep it DNS-only in Cloudflare (not proxied) so the
Google-managed certificate can provision. The record lives in
`infra/cloudflare/drolet-cloud.tf`.

### 3. Check mapping status

```bash
gcloud beta run domain-mappings describe \
  --domain inbox-api.drolet.cloud \
  --region us-central1 \
  --project bens-project-462804
```

GCP provisions the TLS certificate automatically once DNS propagates (can take
15–60 minutes).
