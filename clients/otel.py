import logging
import os

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

_COLLECTOR = "http://otel-collector.observability.svc.cluster.local:4317"


def setup_logging(service_name: str) -> None:
    """Bridge stdlib logging into OTel → collector → Loki. No-ops outside GKE."""
    if not os.environ.get("GCP_PROJECT_ID"):
        return

    provider = LoggerProvider(resource=Resource({"service.name": service_name}))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=_COLLECTOR, insecure=True))
    )
    set_logger_provider(provider)
    logging.getLogger().addHandler(LoggingHandler(logger_provider=provider))
