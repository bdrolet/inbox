# OTel Metrics in GCP Cloud Functions

## The problem with cumulative counters

OpenTelemetry's `OTLPMetricExporter` uses **cumulative temporality** for counters by default. This means each export sends the total accumulated value since the process started — e.g., `counter=1` after processing one email.

Prometheus `increase()` computes the change between the **oldest and newest sample** in the query window. A single data point produces no rate, so `increase()` returns 0. You need at least two samples with different values for the metric to appear in Grafana.

In a long-running server this isn't a problem — the periodic daemon thread exports counter=0, then counter=1, then counter=2, giving a stream of samples. In a Cloud Function, the process handles one or two requests and exits before the 60-second export interval fires.

## The fix: flush before and after each invocation

Export a baseline at the **start** of every invocation, before any work is done. This creates the first sample. Then flush again at the end after incrementing the counter. Grafana sees the before/after pair and `increase()` returns a non-zero value.

```python
@functions_framework.cloud_event
def process(cloud_event: CloudEvent) -> None:
    otel.flush()          # baseline export (counter=N or 0 on cold start)
    try:
        do_work()
        otel.emails_processed.add(1, {"category": "ignore"})
    finally:
        otel.flush()      # post-work export (counter=N+1)
```

For warm invocations, the pre-flush exports the accumulated count from previous emails on this instance (counter=N), and the post-flush exports N+1. Both produce valid deltas.

## Flush implementation

Call `force_flush()` directly on the `PeriodicExportingMetricReader`, not on the `MeterProvider`. The `MeterProvider.force_flush()` delegation to readers is not reliable across all SDK versions.

Store the reader at module level:

```python
# clients/otel.py
_metric_reader: PeriodicExportingMetricReader | None = None

def setup_telemetry(service_name: str) -> None:
    global _metric_reader
    ...
    _metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=..., headers=...),
        export_interval_millis=60_000,
    )
    _meter_provider = MeterProvider(resource=resource, metric_readers=[_metric_reader])

def flush() -> None:
    if _tracer_provider is not None:
        _tracer_provider.force_flush(timeout_millis=5_000)
    if _metric_reader is not None:
        _metric_reader.force_flush(timeout_millis=5_000)
```

Use a 5-second timeout (not 30s) — flush runs twice per invocation, so worst-case overhead is 10s. In practice the OTLP HTTP request completes in under 200ms.

## Why not `shutdown()`?

`MeterProvider.shutdown()` triggers a final collection on the daemon thread and can only be called once. On warm invocations (same Cloud Run instance handling multiple requests), calling `shutdown()` after the first invocation would prevent metrics from being exported on subsequent ones.

## Querying in Grafana

Use `increase()` over a window long enough to capture multiple invocations:

```promql
# Total emails processed in the last hour, by category
sum by (category) (increase(inbox_emails_processed_total[1h]))

# Pipeline errors in the last 24 hours
sum(increase(inbox_pipeline_errors_total[24h]))
```

Avoid very short windows (e.g., `[5m]`) — with sparse traffic there may be no invocations in that window and the query returns empty.

**Instant queries go stale for low-frequency metrics.** A once-a-day emitter like `inbox_sweep_actions_total` (the 5 AM `inbox-sweep` run) flushes a single sample and the container exits. An *instant* query (`/api/v1/query` with no `time`, or Grafana's default "now") only returns series with a sample in the last ~5 minutes, so hours later it returns **0 series** — misleading, not "the metric is missing." Query at the emission time or over a range instead:

```promql
# instant query pinned to just after the 5 AM ET sweep (09:00 UTC):
#   /api/v1/query?query=sum by (action) (inbox_sweep_actions_total)&time=2026-07-15T09:05:00Z
sum by (action) (inbox_sweep_actions_total)   # via query_range over the day
```

## Metric naming: unit expansion

The OTel SDK expands unit abbreviations in metric names when converting to Prometheus format:

| Defined as | Prometheus name |
|---|---|
| `create_histogram("inbox.stage.duration", unit="ms")` | `inbox_stage_duration_milliseconds_bucket` |
| `create_counter("inbox.emails.processed")` | `inbox_emails_processed_total` |
| `create_counter("inbox.sweep.actions")` | `inbox_sweep_actions_total` (label `action`: moved\|held\|skipped\|errored) |
| `create_histogram("inbox.classification.confidence", unit="{score}")` | `inbox_classification_confidence_bucket` |
| `create_histogram("inbox.neighbors.count", unit="{count}")` | `inbox_neighbors_count_bucket` |

Use the expanded names in PromQL queries. To find the actual names in Mimir:
```promql
# In Grafana Explore (Prometheus datasource):
{__name__=~"inbox.*"}
```

Or via the API:
```bash
curl -su "$GRAFANA_PROM_INSTANCE_ID:$GRAFANA_PROM_TOKEN" \
  "$GRAFANA_PROM_URL/api/v1/label/__name__/values" | python3 -m json.tool | grep inbox
```

## Why some metrics appear while others don't

Counter value magnitude matters. In practice, `inbox_claude_tokens_total` (values ~900 per invocation) appeared in Grafana while `inbox_emails_processed_total` (value always 1) showed `increase()` = 0. Both have the same underlying issue — single cumulative data point — but `increase()` over a large value is easier for Mimir to recover from sparse data. The fix (double flush) is still required for correctness of both.

## Making logs visible in Cloud Logging (`force=True`)

Everything in "Debugging missing metrics" below assumes you can *see* the OTel SDK's own error logs. By default, in this runtime, **you cannot** — and neither can you see your own `logger.info` / `logger.error` output.

In the gen2 Cloud Functions runtime the app is served by gunicorn, which configures the root logger **before** `main.py` is imported. So `main.py`'s `logging.basicConfig(level=logging.INFO)` runs against an already-configured root logger and **no-ops** (`basicConfig` does nothing when the root logger already has handlers) — it installs no stderr `StreamHandler`. Python `logging` output then flows only through the handlers already attached — in this project the OTLP `LoggingHandler` added by `otel.setup_telemetry` (→ Grafana), never stderr → Cloud Logging.

**Symptom:** `logger.*` lines are absent from Cloud Logging for *every* function, while startup/infra logs and **direct** stdout/stderr writes (e.g. tqdm's "Loading weights" progress bars, or a bare `print(..., flush=True)`) do show up. That split — framework logging invisible, direct writes visible — is the fingerprint of a missing stderr handler.

**Fix** — force the handler (`main.py`, at import, before `otel.setup_telemetry()`):

```python
logging.basicConfig(
    level=logging.INFO, format="%(levelname)s %(name)s %(message)s", force=True
)
```

`force=True` (Python 3.8+) removes any pre-existing root handlers and installs a fresh stderr handler at INFO, so app logs reach stderr → Cloud Logging. The OTLP handler is still added afterward by `otel.setup_telemetry`, so logs continue to Grafana too — you get both destinations.

**Gotchas:**
- **Does not reproduce locally.** `otel.setup_telemetry` no-ops without `GRAFANA_OTLP_ENDPOINT`, so locally the root logger is empty when `basicConfig` runs and it works fine. The bug only manifests when deployed. Don't trust a local "logs appear" as evidence.
- **App logs land under `resource.type="cloud_run_revision"`** (label `service_name`), *not* `resource.type="cloud_function"` (which carries only infra logs for gen2). Query the app logs with:
  ```bash
  gcloud logging read \
    'resource.type="cloud_run_revision" AND resource.labels.service_name="inbox-sweep"' \
    --project=bens-project-462804 --freshness=10m \
    --format='value(timestamp, severity, textPayload)'
  ```

## Debugging missing metrics

1. **Check the DB first** — `classifications` is the authoritative record of what was processed. Compare against Grafana to quantify the gap. Use `/querying-inbox-db`.
2. **Check GCP logs** — OTLP export errors from `opentelemetry.sdk.metrics._internal.export` (`Exception while exporting metrics`) surface in Cloud Logging **only if the root logger actually has a stderr handler**. `logging.basicConfig(level=logging.INFO)` alone is *not* enough in the gen2/gunicorn runtime — it no-ops because the runtime configures the root logger before `main.py` is imported. You must pass `force=True` (see [Making logs visible in Cloud Logging](#making-logs-visible-in-cloud-logging-forcetrue)). Without it these export errors — and all your own `logger.*` output — are invisible, and you debug blind.
3. **Single data point** — if Grafana shows the metric occasionally but not consistently, the baseline flush is likely missing. A single cumulative data point is invisible to `increase()`.
4. **Auth issues** — the OTLP token in Secret Manager (`grafana-otlp-token`) must be `base64(instance_id:api_key)`. Verify with `gcloud secrets versions access latest --secret=grafana-otlp-token`.
5. **Wrong Prometheus instance ID** — Grafana Cloud has separate numeric IDs for the Grafana dashboard instance and the Prometheus/Mimir datasource. The Prometheus ID is shown in Connections → Data sources → Prometheus → Username / Instance ID. They are different numbers.

## References

Findings from reading the OTel Python SDK v1.42.1 source during debugging:

**`PeriodicExportingMetricReader`** (`opentelemetry/sdk/metrics/_internal/export/__init__.py`)
- Spawns a **daemon thread** on `__init__` that calls `self.collect()` every `export_interval_millis`. Daemon threads are killed when the main thread exits, so in a short-lived Cloud Function the periodic tick almost never fires.
- `force_flush()` calls `super().force_flush()` (→ `collect()` → `_receive_metrics()` → `exporter.export()`) synchronously, then calls `exporter.force_flush()`. The synchronous export path works, but see the note on `OTLPMetricExporter.force_flush()` below.
- `_receive_metrics()` acquires `_export_lock` and calls `self._exporter.export()`. All exceptions are caught and logged with `_logger.exception("Exception while exporting metrics")` — failures are silent unless INFO/ERROR logging is configured on the root logger.
- `shutdown()` sets `_shutdown_event`, which causes the daemon thread to wake early and do one final `collect()` before exiting. It then calls `self._daemon_thread.join()`. This is the only path that guarantees a final export when terminating a process — but it can only be called once, making it unsuitable for warm Cloud Run instances that handle multiple requests.
- The `_export_lock` comment references the OTel spec requirement that `MetricExporter.export()` must never be called concurrently: [opentelemetry-specification/metrics/sdk.md#exportbatch](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/metrics/sdk.md#exportbatch)

**`OTLPMetricExporter.force_flush()`** (`opentelemetry/exporter/otlp/proto/http/metric_exporter/__init__.py`)
- Docstring: *"Nothing is buffered in this exporter, so this method does nothing."* Returns `True` immediately. This means the `exporter.force_flush()` call at the end of `PeriodicExportingMetricReader.force_flush()` is a no-op — the actual export already happened in `_receive_metrics()`.

**`MeterProvider.force_flush()`** (`opentelemetry/sdk/metrics/_internal/__init__.py`)
- Iterates over `self._sdk_config.metric_readers` and calls `reader.force_flush()` on each with a shared deadline. Raises a combined exception if any reader fails. Functionally equivalent to calling `reader.force_flush()` directly for a single-reader setup, but the extra indirection adds a failure mode — call the reader directly.

**Prometheus `increase()` semantics**
- Requires ≥2 samples within the range window to compute a non-zero result. A single cumulative data point returns no value (not 0, but absent/NaN). See: [Prometheus docs — increase()](https://prometheus.io/docs/prometheus/latest/querying/functions/#increase)

**OTel metrics data model — temporality**
- Cumulative temporality: each export sends the running total since process start. Resets when the process restarts (new Cloud Run instance). Mimir handles counter resets via reset detection.
- Delta temporality: each export sends only the delta since the last export. Single-point exports would show the correct delta (1 per email), but OTLP delta sums are stored as Prometheus gauges by the OTLP-to-Prometheus converter, making `increase()` unavailable. Cumulative is correct for this use case.
- See: [OTel spec — Temporality](https://opentelemetry.io/docs/specs/otel/metrics/data-model/#temporality)
