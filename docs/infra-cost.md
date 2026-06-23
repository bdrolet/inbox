# Infrastructure Cost Reference

Cost analysis for the inbox GCP stack, focused on the HTTP Cloud Functions and the trade-offs of migrating to Cloud Run with an API service layer.

## Current HTTP functions

| Function | Memory | Min instances | Max instances | Timeout | Trigger |
|---|---|---|---|---|---|
| `inbox-search` | 512 Mi | 0 | 3 | 60 s | HTTP (on-demand) |
| `inbox-webhook` | 512 Mi | 0 | 3 | 30 s | HTTP (Graph notifications) |
| `inbox-renew` | 512 Mi | 0 | 1 | 60 s | HTTP (Cloud Scheduler) |

## Cloud Functions 2nd gen vs Cloud Run pricing

Cloud Functions 2nd gen runs on Cloud Run infrastructure — the pricing model is identical:

| Dimension | Cloud Functions 2nd gen | Cloud Run |
|---|---|---|
| CPU (active) | $0.0000240 / vCPU-sec | $0.0000240 / vCPU-sec |
| Memory | $0.0000025 / GB-sec | $0.0000025 / GB-sec |
| Requests | $0.40 / million | $0.40 / million |
| Free tier | 2M req + 360k vCPU-sec/mo | Same |
| Scale to zero | Yes | Yes |

At current usage (inbox-search is on-demand only, webhook fires on new emails, renew runs twice a day), all three functions sit comfortably within the free tier. A migration to Cloud Run would have no cost impact.

## "API service" options and their costs

If consolidating HTTP functions behind an API layer:

| Option | Cost | Notes |
|---|---|---|
| Cloud Run direct URL | No additional cost | Same compute pricing, just a different deployment model |
| Cloud API Gateway | $3.50 / million calls after 2M free | Still $0 at current scale |
| Cloud Endpoints (ESPv2 sidecar) | No licensing fee | Sidecar roughly doubles per-request compute cost |
| Apigee | Enterprise pricing | Not appropriate for this scale |

## Where costs could increase

**Minimum instances (`min_instances: 1`)** — eliminates cold start latency by keeping one instance warm. Adds idle compute cost:
- 512 Mi / 0.083 vCPU instance: ~$7–10/month per function
- Only worth it on `inbox-search` if cold start latency is noticeable

**API Gateway tiers** — at current request volume (well under 2M/month), the free tier covers all usage.

## Recommendation

No migration needed for cost reasons. Cloud Functions 2nd gen and Cloud Run are economically equivalent for this workload.

If `inbox-search` cold starts become annoying, set `min_instances = 1` on that function only (~$7–10/month). A full Cloud Run migration is only worth considering if:
- You want to consolidate multiple endpoints into one service (shared DB connection pool, fewer cold starts)
- You need features Cloud Functions doesn't expose (HTTP/2, custom concurrency limits, streaming responses)
