---
name: querying-grafana-metrics
description: Use when checking whether inbox OTel metrics have landed in Grafana Cloud, running a PromQL query against the inbox Prometheus datasource, verifying that a deployed change is emitting metrics, or inspecting metric values and labels for inbox_* series.
---

## Credentials

Stored in `.env` (project root) and `~/src/scripts/zsh/config/.secrets`:

| Env var | Purpose |
|---|---|
| `GRAFANA_PROM_URL` | `https://prometheus-prod-67-prod-us-west-0.grafana.net/prometheus` |
| `GRAFANA_PROM_INSTANCE_ID` | `3286064` |
| `GRAFANA_PROM_TOKEN` | Raw `glc_...` read token |

## Querying

```python
import base64, os, urllib.request, urllib.parse, json
from dotenv import load_dotenv
load_dotenv()  # loads .env from project root

auth = "Basic " + base64.b64encode(
    f"{os.environ['GRAFANA_PROM_INSTANCE_ID']}:{os.environ['GRAFANA_PROM_TOKEN']}".encode()
).decode()
base = os.environ["GRAFANA_PROM_URL"] + "/api/v1"

def query(promql):
    params = urllib.parse.urlencode({"query": promql})
    req = urllib.request.Request(f"{base}/query?{params}", headers={"Authorization": auth})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["data"]["result"]
```

Or with curl:
```bash
source ~/src/scripts/zsh/config/.secrets
curl -su "$GRAFANA_PROM_INSTANCE_ID:$GRAFANA_PROM_TOKEN" \
  --data-urlencode "query=inbox_emails_processed_total" \
  "$GRAFANA_PROM_URL/api/v1/query" | python3 -m json.tool
```

## Key metrics

| Metric | Labels |
|---|---|
| `inbox_emails_processed_total` | `category`, `importance`, `service_name` |
| `inbox_stage_duration_milliseconds_bucket` | `stage` (fetch\|embed\|retrieve_neighbors\|classify\|dispatch\|total) |
| `inbox_claude_tokens_total` | `token_type` (input\|output\|cache_read\|cache_creation) |
| `inbox_classification_confidence_bucket` | `category` |
| `inbox_neighbors_count_bucket` | — |
| `inbox_pipeline_errors_total` | `stage` |
| `inbox_human_feedback_total` | `source`, `category` |
| `inbox_emails_duplicates_total` | — |

## Useful PromQL

```promql
# Total emails by category
sum by (category) (inbox_emails_processed_total)

# p95 pipeline duration
histogram_quantile(0.95, sum by (le) (rate(inbox_stage_duration_milliseconds_bucket{stage="total"}[1h])))

# p95 per stage
histogram_quantile(0.95, sum by (le, stage) (rate(inbox_stage_duration_milliseconds_bucket[1h])))

# Claude token spend rate
sum by (token_type) (rate(inbox_claude_tokens_total[1h]))

# Average classification confidence
histogram_quantile(0.5, sum by (le) (rate(inbox_classification_confidence_bucket[1h])))
```

## Notes

- Mimir ingestion lag is ~10–60s after a flush — if a metric just fired, wait before querying
- `service_name` label is `inbox-process` for Cloud Function invocations, `inbox-process-local-test` for local test runs
- The write token (`GRAFANA_OTLP_TOKEN`) is base64-encoded; the read token (`GRAFANA_PROM_TOKEN`) is the raw `glc_` string
