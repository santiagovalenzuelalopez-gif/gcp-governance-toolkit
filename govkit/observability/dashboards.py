"""Dashboards de Cloud Monitoring generados como código.

- ``platform_overview``: visión de TODA la plataforma con series AGREGADAS (sin agrupar por servicio).
  Agrupar ahí por servicio con decenas de servicios produce decenas de líneas superpuestas, lo contrario de
  una vista general: el desglose por servicio vive en ``service_detail``.
- ``service_detail``: un solo dashboard para cualquier servicio, con una variable de plantilla ``service_name``.
"""

CLOUD_RUN = 'resource.type="cloud_run_revision"'


def _metric(metric: str, resource: str = CLOUD_RUN) -> str:
    return f'metric.type="{metric}" {resource}'


def series(filter_: str, aligner: str, reducer: str | None = "REDUCE_SUM", group_by: list[str] | None = None,
           legend: str | None = None, plot: str = "LINE") -> dict:
    aggregation = {"alignmentPeriod": "60s", "perSeriesAligner": aligner}
    if reducer:
        aggregation["crossSeriesReducer"] = reducer
    if group_by:
        aggregation["groupByFields"] = group_by
    dataset = {"timeSeriesQuery": {"timeSeriesFilter": {"filter": filter_, "aggregation": aggregation}}, "plotType": plot}
    if legend:
        dataset["legendTemplate"] = legend
    return dataset


def tile(x: int, y: int, title: str, datasets: list[dict], thresholds: list[tuple[float, str]] | None = None,
         width: int = 6, height: int = 4) -> dict:
    chart: dict = {"dataSets": datasets}
    if thresholds:
        chart["thresholds"] = [{"value": value, "label": label} for value, label in thresholds]
    return {"width": width, "height": height, "xPos": x, "yPos": y, "widget": {"title": title, "xyChart": chart}}


def _layout(tiles: list[dict]) -> dict:
    return {"columns": 12, "tiles": tiles}


def platform_overview(title: str = "Plataforma — Vista general", with_vpc: bool = True,
                      error_5xx_threshold: float = 5) -> dict:
    rc, lat = "run.googleapis.com/request_count", "run.googleapis.com/request_latencies"
    tiles = [
        tile(0, 0, "Requests por segundo (plataforma)", [series(_metric(rc), "ALIGN_RATE")]),
        # Se llama "Tasa de 5xx (req/s)" porque eso es lo que mide: una tasa absoluta, NO un porcentaje.
        tile(6, 0, "Tasa de 5xx (req/s)", [series(_metric(rc, f'{CLOUD_RUN} metric.label.response_code_class="5xx"'), "ALIGN_RATE")],
             [(error_5xx_threshold, f"Alerta: {error_5xx_threshold:g} req/s")]),
        tile(0, 4, "Latencia p95 (plataforma)", [series(_metric(lat), "ALIGN_PERCENTILE_95", "REDUCE_MEAN")]),
        tile(6, 4, "Instancias activas", [series(_metric("run.googleapis.com/container/instance_count"), "ALIGN_MEAN")]),
        tile(0, 8, "Errores de aplicación (ERROR/CRITICAL)",
             [series(_metric("logging.googleapis.com/user/application_errors"), "ALIGN_RATE")]),
        tile(6, 8, "Errores de base de datos",
             [series(_metric("logging.googleapis.com/user/db_connection_errors"), "ALIGN_RATE")]),
    ]
    if with_vpc:
        vpc = 'resource.type="vpc_access_connector"'
        by_connector = ["resource.label.connector_name"]
        tiles.append(tile(0, 12, "VPC Connector — bytes enviados / recibidos", [
            # un conector por línea y con leyenda: sin groupBy se combinarían todos en una sola serie
            series(_metric("vpcaccess.googleapis.com/connector/sent_bytes_count", vpc), "ALIGN_RATE", "REDUCE_SUM",
                   by_connector, "{{resource.label.connector_name}} · enviados"),
            series(_metric("vpcaccess.googleapis.com/connector/received_bytes_count", vpc), "ALIGN_RATE", "REDUCE_SUM",
                   by_connector, "{{resource.label.connector_name}} · recibidos"),
        ], width=12))
    return {"displayName": title, "labels": {"template": "platform-overview"}, "mosaicLayout": _layout(tiles)}


def service_detail(title: str = "Servicio — Detalle") -> dict:
    lat, rc = "run.googleapis.com/request_latencies", "run.googleapis.com/request_count"
    tiles = [
        tile(0, 0, "CPU + Memoria (ref. 80% / 85%)", [
            series(_metric("run.googleapis.com/container/cpu/utilizations"), "ALIGN_PERCENTILE_99", "REDUCE_MEAN", legend="CPU p99"),
            series(_metric("run.googleapis.com/container/memory/utilizations"), "ALIGN_PERCENTILE_99", "REDUCE_MEAN", legend="Memoria p99"),
        ], [(0.8, "CPU 80%"), (0.85, "Mem 85%")]),
        # Las tres series comparten filtro y metric.type: sin legendTemplate son indistinguibles en la leyenda
        tile(6, 0, "Latencia p50 / p95 / p99", [
            series(_metric(lat), f"ALIGN_PERCENTILE_{p}", "REDUCE_MEAN", legend=f"p{p}") for p in (50, 95, 99)
        ]),
        tile(0, 4, "Requests por response_code",
             [series(_metric(rc), "ALIGN_RATE", "REDUCE_SUM", ["metric.label.response_code"], plot="STACKED_BAR")]),
        tile(6, 4, "Instancias", [series(_metric("run.googleapis.com/container/instance_count"), "ALIGN_MEAN", "REDUCE_SUM",
                                         ["metric.label.state"], "{{metric.label.state}}")]),
        tile(0, 8, "Errores de aplicación", [series(_metric("logging.googleapis.com/user/application_errors"), "ALIGN_RATE")]),
        tile(6, 8, "Arranques en frío (esperados con min-instances=0)",
             [series(_metric("run.googleapis.com/container/startup_latencies"), "ALIGN_COUNT")]),
    ]
    return {
        "displayName": title,
        "labels": {"template": "service-detail"},
        # Variable de plantilla: el MISMO dashboard sirve para cualquier servicio y filtra todos los widgets
        "dashboardFilters": [{"filterType": "RESOURCE_LABEL", "labelKey": "service_name", "templateVariable": "service_name"}],
        "mosaicLayout": _layout(tiles),
    }
