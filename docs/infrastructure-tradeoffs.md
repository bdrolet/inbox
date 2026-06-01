# Infrastructure Tradeoffs

Architecture and cost analysis for the Inbox processing worker. Documents every option considered, the constraints that ruled each in or out, and the final decisions.

---

## Background

The core problem: when a new email arrives, we need to run a pipeline that takes ~15 seconds (fetch, embed, classify, write to DB). The pipeline needs access to a Postgres database with pgvector. It needs to trigger immediately when mail arrives, not on a schedule.

Two independent decisions shape the cost:

1. **How does the worker run?** (cron job, always-on service, or scale-to-zero)
2. **Where does the database live?** (GKE, Cloud SQL, RDS)

These interact: not every worker option can reach every database option.

---

## Decision 1: Trigger mechanism — Cron vs Webhooks

### Original: Cloud Run Job on a daily cron

The existing system runs as a Cloud Run Job triggered by Cloud Scheduler at 8 AM EST. It fetches all inbox emails and classifies them in a batch.

**Problems with cron:**
- Up to 24-hour delay between email arrival and triage
- Reprocesses messages already seen (requires dedup logic)
- Stateless — no memory of prior runs

### Chosen: Microsoft Graph change notifications (webhooks)

Microsoft Graph supports push notifications: when a new message arrives in the inbox, Graph POSTs a notification to a registered HTTPS endpoint within seconds. The subscription is registered once and renewed every ~3 days (Graph's maximum expiry for mail resources).

**Key detail — rich notifications:** Graph supports `includeResourceData: true` on the subscription, which embeds the message body in the notification payload. This avoids a separate fetch call after receipt. However, rich notifications require providing an encryption certificate — adding setup complexity. For Phase 1, we fetch the message by ID after receiving the notification, and can add rich notifications later.

**Webhook validation:** When a subscription is first registered (and on renewal), Graph sends a GET request with a `validationToken` query parameter. The endpoint must echo it as `text/plain` within 10 seconds. This constraint drove the decision to keep an always-on public endpoint (Cloud Function) separate from the scale-to-zero worker.

---

## Decision 2: Worker topology

Five options were evaluated, from always-on to fully serverless.

### Option A: GKE Deployment (always-on)

Run an always-on Kubernetes Deployment inside the GKE cluster. The pod runs an HTTP server that receives webhook POSTs directly via the GKE Gateway.

**Pros:**
- Direct ClusterIP access to GKE Postgres — no connectivity bridge
- bge embedding model loaded once at startup, no cold start per message
- No Pub/Sub needed

**Cons:**
- Pod runs 24/7 regardless of message volume
- At 1 vCPU / 2GB RAM on GKE Autopilot: ~$39/month always-on
- Webhook validation (10s window) is risky if the pod ever restarts while a subscription renewal fires

**Cost:**
| Volume | Monthly cost |
|--------|-------------|
| 100/day | ~$39 |
| 1,000/day | ~$39 |

Verdict: rejected. Fixed cost with no benefit at low volume.

---

### Option B: GKE Deployment + KEDA (scale to zero)

Same as Option A but with KEDA watching the Pub/Sub queue depth. When messages are present, KEDA scales the Deployment from 0 → 1. When the queue drains, it scales back to 0 after a cooldown period.

A thin Cloud Function sits in front to handle the public webhook endpoint and publish to Pub/Sub. This solves the validation handshake problem — the Cloud Function is always available regardless of whether the GKE pod is running.

```
Graph → Cloud Function → Pub/Sub ──(KEDA)──► GKE Deployment → GKE Postgres
```

**Cold start:** GKE Autopilot cold start = 30 seconds (if a node is already provisioned) to 3 minutes (if Autopilot needs to provision a new node). Acceptable for email triage.

**The scale-to-zero breakpoint:** KEDA only actually scales to zero if the Pub/Sub queue is empty for longer than the cooldown period. At 100 emails/day (one every ~14 minutes), the pod scales to zero between messages. At 1,000/day (one every ~86 seconds), emails arrive faster than the cooldown — the pod effectively runs 24/7.

**Cost:**
| Volume | Emails per minute | Pod behavior | Monthly cost |
|--------|------------------|--------------|-------------|
| 100/day | ~0.07/min | Scales to zero between emails | ~$5.50 |
| 1,000/day | ~0.70/min | Stays up ~24/7 (never hits cooldown) | ~$39 |

**Chosen for Phase 1** — uses existing GKE Postgres with no new infrastructure except the Cloud Function and Pub/Sub topic.

---

### Option C: Cloud Run Service (scale to zero)

Replace the GKE Deployment with a Cloud Run Service. Cloud Run is serverless on GCP — scales to zero when idle, bills only during active request processing.

**Problem:** Cloud Run cannot reach GKE's ClusterIP Postgres service. Two bridge options:
- **Internal Load Balancer:** exposes Postgres on a VPC-internal IP reachable via Cloud Run's Direct VPC Egress. Cost: ~$18/month for the ILB forwarding rule (always-on).
- **Cloud SQL:** move the database. Cost: ~$9–10/month.

The ILB approach costs more than Cloud SQL and adds operational complexity. Cloud SQL is the right choice if going serverless.

**Cost with Cloud SQL:**
| Volume | CF + Cloud SQL | Notes |
|--------|---------------|-------|
| 100/day | ~$10/month | Compute within free tier |
| 1,000/day | ~$18/month | Compute exceeds free tier |

---

### Option D: Cloud Function (processing, not just ingestion)

Instead of routing to a GKE pod or Cloud Run service, process each message directly inside a Cloud Function triggered by Pub/Sub. The same Cloud Function that receives the Graph webhook publishes to Pub/Sub; a second Pub/Sub-triggered Cloud Function runs the pipeline.

This requires Cloud SQL for the database (same connectivity constraint as Option C).

**Pros:**
- Simplest architecture — no GKE, no KEDA, no k8s manifests
- Faster cold start than GKE Autopilot: 3–6 seconds (container + bge model load)
- Bills only during active processing
- Pub/Sub-triggered CF is cleaner than a pull loop in a pod

**The free tier advantage:**
Cloud Functions have a generous permanent free tier: 2M requests, 180K vCPU-seconds, and 360K GB-seconds per month. At 100 emails/day, you never exceed it. At 1,000/day you exceed the CPU and memory tiers but only slightly.

**Cost with Cloud SQL:**
| Volume | CF compute | Cloud SQL | Total |
|--------|-----------|-----------|-------|
| 100/day | $0 (free tier) | ~$10 | ~$10 |
| 1,000/day | ~$7.83 | ~$10 | ~$18 |

---

### Option E: Always-on GKE pod, no KEDA

Run the worker as a Deployment that's always on (replicas: 1). Simpler than KEDA but costs the same as Option A regardless of volume. Ruled out for the same reason — fixed cost with no benefit at low volume.

---

## Decision 3: Database location

The database must run Postgres 15+ with pgvector. Three options were evaluated.

### GKE Postgres (existing)

A single-replica Postgres 16 pod running in the `apps` namespace. Deployed as a Kubernetes Deployment with a 10Gi PVC.

**Specs:** 250m CPU / 512Mi RAM (requests), 500m / 1Gi (limits)
**Connection:** `postgres.apps.svc.cluster.local:5432` — ClusterIP, in-cluster only
**Cost:** ~$10/month on GKE Autopilot (already running)
**pgvector:** not yet installed (one-shot Job: `CREATE EXTENSION IF NOT EXISTS vector;`)

**Key constraint:** A ClusterIP service is not a real VPC IP — it's a virtual address maintained by kube-proxy inside the cluster. Nothing outside the cluster (Cloud Functions, Cloud Run) can reach it without a bridge.

**To bridge externally:**
| Bridge | Cost | Verdict |
|--------|------|---------|
| Internal Load Balancer | ~$18/month (always-on forwarding rule) | More expensive than Cloud SQL |
| Serverless VPC Access | Cannot reach ClusterIP (only pod IPs and VMs) | Not viable |
| Direct VPC Egress + ILB | Still requires ILB | Same problem |

**Accessible from:** GKE Deployments only (unless bridged at extra cost).

---

### Cloud SQL for PostgreSQL (GCP)

Managed Postgres on GCP. Supports pgvector on Postgres 14+. Automatic backups, point-in-time recovery, minor version patching.

**Smallest viable instance:** db-f1-micro (0.6GB RAM, shared vCPU, 10GB SSD)
**Cost:** ~$10/month (instance + storage)
**Connection from Cloud Functions/Cloud Run:** via Cloud SQL Auth Proxy (built into the runtime) — no VPC configuration required
**pgvector:** enabled via `CREATE EXTENSION IF NOT EXISTS vector;` after provisioning

**Trade-off vs GKE Postgres:**
- $10/month vs $0 incremental (if GKE Postgres is already running)
- If GKE Postgres serves no other workloads and is removed: net $0 change on the bill
- Cloud SQL has managed backups; GKE Postgres has no backup configured

---

### Amazon RDS for PostgreSQL (AWS)

Managed Postgres on AWS. Supports pgvector on Postgres 14.5+.

**Smallest viable instance:** db.t3.micro (1GB RAM, 2 vCPU, 10GB SSD)
**Cost:** ~$13.50/month after the 12-month free tier (db.t3.micro is free for the first year)
**Connection from Lambda:** Lambda must run inside a VPC to reach RDS; no additional cost since AWS removed ENI pricing for Lambda in VPC in 2022

**Trade-off vs Cloud SQL:**
- More expensive (~$3.50/month more) after free tier
- 12-month free tier is a real advantage for new projects
- Operationally equivalent (both managed, both support pgvector)

---

## Decision 4: Cloud provider comparison

At steady state (Phase 3, ~15s/message, 2GB RAM, 1 vCPU), GCP and AWS are architecturally equivalent:

```
Graph → HTTP Function → Queue → Processing Function → Managed Postgres
         (CF/Lambda)  (Pub/Sub/SQS)  (CF/Lambda)     (Cloud SQL/RDS)
```

### At 100 emails/day (3,000/month)

| Option | Compute | Database | Total |
|--------|---------|----------|-------|
| GCP GKE KEDA | ~$5.50 | $0 (GKE, existing) | **~$5.50** |
| GCP Cloud Function + Cloud SQL | $0 (free tier) | ~$10 | **~$10** |
| AWS Lambda + RDS | $0 (free tier) | $0 (free tier, yr 1) / ~$13.50 (yr 2+) | **$0 / ~$13.50** |

At 100/day, GKE KEDA is cheapest if you're already in GCP — you pay only for the pod runtime (~$5.50/month) and the existing Postgres is already on the bill. Cloud Function + Cloud SQL costs more because you're paying for Cloud SQL whether or not messages arrive.

---

### At 1,000 emails/day (30,000/month)

| Option | Compute | Database | Total |
|--------|---------|----------|-------|
| GCP GKE KEDA | ~$39 (pod ~24/7) | $0 (GKE, existing) | **~$39** |
| GCP Cloud Function + Cloud SQL | ~$7.83 | ~$10 | **~$17.83** |
| AWS Lambda + RDS | ~$8.33 | $0 (free tier, yr 1) / ~$13.50 (yr 2+) | **$8.33 / ~$21.83** |

At 1,000/day, KEDA's scale-to-zero stops working — emails arrive every 86 seconds, faster than the 5-minute cooldown, so the pod runs continuously at ~$39/month. Cloud Function wins on GCP. AWS is slightly more expensive than GCP after the free tier due to RDS pricing.

---

### Breakeven: when does GKE KEDA beat Cloud Function?

GKE KEDA cost = pod compute only (scales with actual usage)
Cloud Function + Cloud SQL = ~$10 fixed (Cloud SQL) + small variable compute

KEDA is cheaper when pod compute < $10/month, which means the pod runs less than ~25% of the time. At a 5-minute cooldown, that's roughly 6 hours of pod runtime per day — approximately **200 emails/day** or fewer (assuming even distribution throughout the day).

**Below ~200/day → GKE KEDA is cheaper (if GKE Postgres already running)**
**Above ~200/day → Cloud Function + Cloud SQL is cheaper**

---

### Volume sensitivity summary

| Emails/day | GKE KEDA | CF + Cloud SQL | AWS Lambda + RDS |
|-----------|----------|----------------|-----------------|
| 50 | ~$2 | ~$10 | ~$0 (free yr 1) / ~$13.50 |
| 100 | ~$5.50 | ~$10 | ~$0 / ~$13.50 |
| 200 | ~$11 | ~$10 | ~$0 / ~$13.50 |
| 500 | ~$25 | ~$12 | ~$2 / ~$15.50 |
| 1,000 | ~$39 | ~$18 | ~$8 / ~$22 |
| 2,000 | ~$39 | ~$26 | ~$17 / ~$30.50 |

_GKE KEDA costs plateau at ~$39/month (pod runs 24/7). CF and Lambda scale linearly above the free tier. RDS and Cloud SQL are fixed regardless of volume._

---

## Hybrid architectures considered

Three hybrid approaches were evaluated to get the best of GKE networking (direct Postgres access) with serverless economics.

### Hybrid A: Cloud Function webhook + Pub/Sub → GKE Deployment (KEDA)

```
Graph → Cloud Function → Pub/Sub → GKE Deployment (KEDA) → GKE Postgres
```

This is the **chosen architecture for Phase 1.** The Cloud Function is a thin public endpoint (validates handshake, publishes to Pub/Sub). The GKE pod pulls from Pub/Sub and does all processing. The pod scales to zero when idle.

**Why this over pure Cloud Function:** Keeps the existing GKE Postgres. No Cloud SQL cost. Works well at the expected email volume.

---

### Hybrid B: Cloud Function + Pub/Sub → Cloud Run + GKE Postgres via ILB

```
Graph → Cloud Function → Pub/Sub → Cloud Run (VPC Egress) → ILB → GKE Postgres
```

Scale-to-zero compute (Cloud Run) combined with keeping GKE Postgres — but the Internal Load Balancer required to expose Postgres costs ~$18/month regardless of traffic. More expensive than Cloud SQL ($10/month) for the same outcome.

**Verdict:** Not worth it. Cloud SQL is cheaper than the ILB.

---

### Hybrid C: Pure Cloud Function + Cloud SQL (no GKE for processing)

```
Graph → Cloud Function → Pub/Sub → Cloud Function → Cloud SQL
```

No GKE pods at all. Simplest architecture, fastest cold starts, lowest cost above ~200 emails/day. If GKE Postgres is removed, the overall bill is comparable or lower.

**Trade-off:** Requires Cloud SQL (new $10/month cost). Loses the GKE Postgres reuse. Best choice if inbox volume grows or if you want to eliminate GKE complexity from this workload.

---

## Summary of all options

| Architecture | Database | Cold start | 100/day | 1,000/day | Best for |
|-------------|----------|-----------|---------|-----------|----------|
| GKE always-on | GKE Postgres | None | $39 | $39 | Never |
| GKE KEDA | GKE Postgres | 30s–3min | $5.50 | $39 | <200/day on GCP (if GKE Postgres already running) |
| **CF + Cloud SQL (GCP) (chosen)** | **Cloud SQL** | **3–6s** | **$10** | **$18** | **All volumes on GCP** |
| Lambda + RDS (AWS) | RDS | 1–3s | $0* / $13.50 | $8* / $22 | New projects (free tier) |
| CF + GKE Postgres via ILB | GKE Postgres | 3–6s | $28 | $28 | Never |

_\* First 12 months via AWS free tier._

---

## Final decision and rationale

**Chosen: Cloud Function + Cloud SQL** — the GKE Postgres pod was removed in favour of a managed Cloud SQL instance (db-f1-micro, ~$10/month). At ~100 emails/day CF compute is within the free tier; only Cloud SQL is billed. No KEDA, no k8s worker, no Docker image builds for the processor.

**If volume grows past ~1,000/day:** cost rises to ~$18/month as CF compute exceeds the free tier. Cloud SQL remains fixed. Still cheaper and simpler than the GKE KEDA path at that volume.

**AWS:** viable alternative at any scale. RDS t3.micro is more expensive than Cloud SQL f1-micro after the free tier (~$3.50/month difference). Prefer if the rest of your infrastructure is already AWS.
