import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_meter_provider: MeterProvider | None = None
_tracer_provider: TracerProvider | None = None
_metric_reader: PeriodicExportingMetricReader | None = None

# Metric instruments — no-ops until setup_telemetry() runs
emails_processed: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
emails_duplicates: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
pipeline_errors: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
claude_tokens: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
human_feedback: metrics.Counter = metrics.NoOpMeter("noop").create_counter("noop")
confidence_hist: metrics.Histogram = metrics.NoOpMeter("noop").create_histogram("noop")
stage_duration: metrics.Histogram = metrics.NoOpMeter("noop").create_histogram("noop")
neighbors_hist: metrics.Histogram = metrics.NoOpMeter("noop").create_histogram("noop")


def setup_telemetry(service_name: str) -> None:
    """
    Initialize OTel MeterProvider, TracerProvider, and LoggerProvider targeting
    Grafana Cloud OTLP. No-ops when GRAFANA_OTLP_ENDPOINT is unset (local dev).
    """
    global _meter_provider, _tracer_provider, _metric_reader
    global emails_processed, emails_duplicates, pipeline_errors, claude_tokens
    global human_feedback, confidence_hist, stage_duration, neighbors_hist

    endpoint = os.environ.get("GRAFANA_OTLP_ENDPOINT")
    if not endpoint:
        return

    token = os.environ.get("GRAFANA_OTLP_TOKEN", "")
    headers = {"Authorization": f"Basic {token}"}
    resource = Resource({"service.name": service_name})

    # --- Traces ---
    _tracer_provider = TracerProvider(resource=resource)
    _tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers))
    )
    trace.set_tracer_provider(_tracer_provider)

    # --- Metrics ---
    _metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers),
        export_interval_millis=60_000,
    )
    _meter_provider = MeterProvider(resource=resource, metric_readers=[_metric_reader])
    metrics.set_meter_provider(_meter_provider)

    meter = _meter_provider.get_meter(service_name)
    emails_processed = meter.create_counter(
        "inbox.emails.processed", description="Emails successfully processed"
    )
    emails_duplicates = meter.create_counter(
        "inbox.emails.duplicates", description="Duplicate emails skipped"
    )
    pipeline_errors = meter.create_counter(
        "inbox.pipeline.errors", description="Pipeline errors by stage"
    )
    claude_tokens = meter.create_counter(
        "inbox.claude.tokens", description="Claude API tokens consumed"
    )
    human_feedback = meter.create_counter(
        "inbox.human.feedback", description="Human label feedback events"
    )
    confidence_hist = meter.create_histogram(
        "inbox.classification.confidence",
        unit="{score}",
        description="Classification confidence score",
    )
    stage_duration = meter.create_histogram(
        "inbox.stage.duration", unit="ms", description="Duration of each pipeline stage"
    )
    neighbors_hist = meter.create_histogram(
        "inbox.neighbors.count",
        unit="{count}",
        description="Labeled neighbors retrieved for classification",
    )

    # --- Logs ---
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", headers=headers))
    )
    set_logger_provider(log_provider)
    logging.getLogger().addHandler(LoggingHandler(logger_provider=log_provider))

    logger.debug("OTel telemetry configured for service=%s endpoint=%s", service_name, endpoint)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("inbox")


def flush() -> None:
    """Force-flush all providers. Call before and after every Cloud Function invocation."""
    if _tracer_provider is not None:
        _tracer_provider.force_flush(timeout_millis=5_000)
    if _metric_reader is not None:
        _metric_reader.force_flush(timeout_millis=5_000)
