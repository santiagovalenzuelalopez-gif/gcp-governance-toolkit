"""Métricas basadas en logs (log-based metrics): convierten logs JSON estructurados en series alertables."""

from dataclasses import dataclass

CLOUD_RUN = 'resource.type="cloud_run_revision"'
DEFAULT_DB_ERROR_PATTERN = r"(MongoServerError|sqlalchemy\.exc|OperationalError|connection refused)"


@dataclass(frozen=True)
class LogMetric:
    name: str
    description: str
    filter: str


def default_metrics(db_error_pattern: str = DEFAULT_DB_ERROR_PATTERN) -> list[LogMetric]:
    return [
        LogMetric(
            "application_errors",
            "Errores de aplicación desde logs JSON estructurados",
            f'{CLOUD_RUN} AND (severity="ERROR" OR severity="CRITICAL")',
        ),
        LogMetric(
            "unhandled_exceptions",
            "Excepciones no manejadas (el campo 'exception' lo agrega el formatter JSON del estándar)",
            f'{CLOUD_RUN} AND jsonPayload.exception!=""',
        ),
        LogMetric(
            "db_connection_errors",
            "Errores de conectividad a la base de datos",
            f'{CLOUD_RUN} AND jsonPayload.message=~"{db_error_pattern}"',
        ),
    ]
