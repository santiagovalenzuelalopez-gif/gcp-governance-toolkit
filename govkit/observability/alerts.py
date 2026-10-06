"""Políticas de alerta de Cloud Monitoring generadas como código.

Principios de diseño (cada uno corresponde a un problema real; ver docs/OBSERVABILIDAD.md):

1. Toda alerta debe ser ACCIONABLE: sostenida (``duration``) y con documentación de diagnóstico.
2. Una política por métrica, NO una por servicio, pero agrupada con ``groupByFields``: sin agrupar,
   ``crossSeriesReducer`` colapsa todos los servicios en una sola serie, el incidente no dice cuál falló
   y un servicio saturado se diluye al promediarse con los sanos.
3. Los umbrales de saturación se calculan sobre el máximo real del recurso, no sobre un porcentaje inventado.
"""

from dataclasses import dataclass, field

CLOUD_RUN = 'resource.type="cloud_run_revision"'
BYTES_PER_MBPS = 125_000  # 1 Mbps = 125.000 bytes/s


@dataclass(frozen=True)
class AlertThresholds:
    error_5xx_per_second: float = 5
    latency_p95_ms: float = 3000
    memory_utilization: float = 0.85
    cpu_utilization: float = 0.80
    db_errors_per_second: float = 5
    vpc_saturation: float = 0.80
    sustained_short: str = "300s"  # latencia, 5xx, errores
    sustained_long: str = "600s"   # CPU y memoria: picos breves son normales


@dataclass(frozen=True)
class AlertSpec:
    name: str
    filter: str
    threshold: float
    duration: str
    aligner: str
    reducer: str
    group_by: str
    documentation: str
    comparison: str = "COMPARISON_GT"

    def to_policy(self) -> dict:
        return {
            "displayName": self.name,
            "combiner": "OR",
            "documentation": {"content": self.documentation, "mimeType": "text/markdown"},
            "conditions": [
                {
                    "displayName": f"{self.name} — condición",
                    "conditionThreshold": {
                        "filter": self.filter,
                        "comparison": self.comparison,
                        "thresholdValue": self.threshold,
                        "duration": self.duration,
                        "aggregations": [
                            {
                                "alignmentPeriod": "60s",
                                "perSeriesAligner": self.aligner,
                                "crossSeriesReducer": self.reducer,
                                "groupByFields": [self.group_by],
                            }
                        ],
                    },
                }
            ],
        }


@dataclass(frozen=True)
class AlertConfig:
    project: str
    dashboard_url: str = ""
    thresholds: AlertThresholds = field(default_factory=AlertThresholds)
    vpc_connector_max_mbps: float | None = 1000  # None = el proyecto no usa conectores VPC


def _link(config: AlertConfig) -> str:
    return f" Dashboard: {config.dashboard_url}" if config.dashboard_url else ""


def vpc_threshold_bytes(max_mbps: float, saturation: float) -> float:
    return max_mbps * BYTES_PER_MBPS * saturation


def default_alerts(config: AlertConfig) -> list[AlertSpec]:
    t, p, link = config.thresholds, config.project, _link(config)
    by_service = "resource.label.service_name"
    specs = [
        AlertSpec(
            f"Cloud Run — Tasa de error 5xx elevada — {p}",
            f'metric.type="run.googleapis.com/request_count" {CLOUD_RUN} metric.label.response_code_class="5xx"',
            t.error_5xx_per_second, t.sustained_short, "ALIGN_RATE", "REDUCE_SUM", by_service,
            f"Tasa de respuestas 5xx > {t.error_5xx_per_second:g} req/s sostenida 5 minutos en un servicio concreto "
            f"(ver resource.label.service_name en el incidente). Diagnóstico: logs del servicio y dashboard Service Detail "
            f"filtrado por ese servicio.{link}",
        ),
        AlertSpec(
            f"Cloud Run — Latencia p95 degradada — {p}",
            f'metric.type="run.googleapis.com/request_latencies" {CLOUD_RUN}',
            t.latency_p95_ms, t.sustained_short, "ALIGN_PERCENTILE_95", "REDUCE_MEAN", by_service,
            f"Latencia p95 > {t.latency_p95_ms:g} ms sostenida 5 minutos en un servicio concreto. Diagnóstico: CPU/memoria "
            f"y arranques en frío del servicio en el dashboard Service Detail.{link}",
        ),
        AlertSpec(
            f"Cloud Run — Memoria alta — {p}",
            f'metric.type="run.googleapis.com/container/memory/utilizations" {CLOUD_RUN}',
            t.memory_utilization, t.sustained_long, "ALIGN_PERCENTILE_99", "REDUCE_MEAN", by_service,
            f"Uso de memoria > {t.memory_utilization:.0%} sostenido 10 minutos en un servicio concreto. Riesgo de OOM kill. "
            f"Diagnóstico: ¿subir el límite de memoria o hay una fuga?{link}",
        ),
        AlertSpec(
            f"Cloud Run — CPU alta — {p}",
            f'metric.type="run.googleapis.com/container/cpu/utilizations" {CLOUD_RUN}',
            t.cpu_utilization, t.sustained_long, "ALIGN_PERCENTILE_99", "REDUCE_MEAN", by_service,
            f"Uso de CPU > {t.cpu_utilization:.0%} sostenido 10 minutos en un servicio concreto. Diagnóstico: pico de "
            f"tráfico, bucle ineficiente o límite de CPU insuficiente.{link}",
        ),
        AlertSpec(
            f"Cloud Run — Errores de aplicación sostenidos — {p}",
            f'metric.type="logging.googleapis.com/user/application_errors" {CLOUD_RUN}',
            0, t.sustained_short, "ALIGN_RATE", "REDUCE_SUM", by_service,
            "Errores ERROR/CRITICAL sostenidos 5 minutos continuos en un servicio concreto. Un error aislado NO dispara "
            f"esta alerta. Diagnóstico: logs del servicio en Cloud Logging.{link}",
        ),
        AlertSpec(
            f"Cloud Run — Errores de base de datos sostenidos — {p}",
            f'metric.type="logging.googleapis.com/user/db_connection_errors" {CLOUD_RUN}',
            t.db_errors_per_second, t.sustained_short, "ALIGN_RATE", "REDUCE_SUM", by_service,
            f"Errores de conexión a la base de datos sostenidos 5 minutos en un servicio concreto. Diagnóstico: estado de "
            f"la BD y logs de conexión del servicio.{link}",
        ),
    ]
    if config.vpc_connector_max_mbps:
        threshold = vpc_threshold_bytes(config.vpc_connector_max_mbps, t.vpc_saturation)
        specs.append(
            AlertSpec(
                f"VPC Connector — Saturación de egress — {p}",
                'metric.type="vpcaccess.googleapis.com/connector/sent_bytes_count" resource.type="vpc_access_connector"',
                threshold, t.sustained_short, "ALIGN_RATE", "REDUCE_SUM", "resource.label.connector_name",
                f"Egress de un conector VPC > {t.vpc_saturation:.0%} de su throughput máximo "
                f"({config.vpc_connector_max_mbps:g} Mbps) sostenido 5 minutos. Ver resource.label.connector_name para "
                f"saber cuál. Agrupada por conector: promediar entre conectores diluiría la saturación de uno solo.{link}",
            )
        )
    return specs
