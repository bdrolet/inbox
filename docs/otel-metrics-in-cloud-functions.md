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

## Debugging missing metrics

1. **Check the DB first** — `classifications` is the authoritative record of what was processed. Compare against Grafana to quantify the gap.
2. **Check GCP logs** — with `logging.basicConfig(level=logging.INFO)` set in `main.py`, OTLP export errors from `opentelemetry.sdk.metrics._internal.export` will appear in `run.googleapis.com/stderr` as `ERROR` entries. Look for `Exception while exporting metrics`.
3. **Single data point** — if Grafana shows the metric occasionally but not consistently, the baseline flush is likely missing. A single cumulative data point is invisible to `increase()`.
4. **Auth issues** — the OTLP token in Secret Manager (`grafana-otlp-token`) must be `base64(instance_id:api_key)`. Verify with `gcloud secrets versions access latest --secret=grafana-otlp-token`.

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
