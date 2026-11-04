"""Azure Application Insights observability via OpenTelemetry.

Configures Azure Monitor to export traces, metrics, and logs from the
FastAPI backend to Application Insights.
"""

import logging
import os

from app.config import settings

logger = logging.getLogger(__name__)


def setup_observability() -> None:
    """Configure Azure Monitor OpenTelemetry and enable MAF instrumentation.

    Must be called BEFORE the FastAPI app is created and before any agents
    are instantiated so that all downstream spans are captured.

    If APPLICATION_INSIGHTS_CONNECTION_STRING is not set, observability
    is silently skipped so the app can still run locally without App Insights.
    """
    connection_string = settings.APPLICATION_INSIGHTS_CONNECTION_STRING
    if not connection_string:
        logger.info(
            "APPLICATION_INSIGHTS_CONNECTION_STRING not set — "
            "observability disabled"
        )
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        # Sets the cloud role name shown on the Application Map node.
        # Use setdefault so an explicit OTEL_SERVICE_NAME env var always wins.
        os.environ.setdefault("OTEL_SERVICE_NAME", "prior-auth-backend")
        configure_azure_monitor(
            connection_string=connection_string,
            enable_live_metrics=True,
        )

        logger.info("Azure Application Insights observability enabled")
    except ImportError as exc:
        logger.warning(
            "Observability packages not installed — skipping: %s", exc
        )
    except Exception as exc:
        logger.warning(
            "Failed to configure observability — skipping: %s", exc
        )
