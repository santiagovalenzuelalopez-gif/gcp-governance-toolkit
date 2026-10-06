import json

import pytest

from govkit.cli import main
from govkit.observability.alerts import AlertConfig, AlertThresholds, default_alerts, vpc_threshold_bytes
from govkit.observability.apply import RUNTIME_ROLES, apply_all, verify_runtime_iam
from govkit.observability.budget import budget_amount, budget_create_args, cleanup_policy
from govkit.observability.dashboards import platform_overview, service_detail
from govkit.observability.metrics import default_metrics
from govkit.observability.render import render_all, slug

CHANNEL = "projects/demo/notificationChannels/123"


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def alerts(**kwargs):
    return default_alerts(AlertConfig(project="demo", **kwargs))


# --- alertas: invariantes de diseño --------------------------------------------------------------------------


def test_every_alert_is_grouped_so_the_incident_names_the_affected_resource():
    """Sin groupByFields, crossSeriesReducer colapsa TODOS los servicios en una sola serie."""
    for spec in alerts():
        aggregation = spec.to_policy()["conditions"][0]["conditionThreshold"]["aggregations"][0]
        assert aggregation["groupByFields"] and aggregation["crossSeriesReducer"], spec.name


def test_cloud_run_alerts_group_by_service_and_vpc_by_connector():
    by_name = {s.name: s.group_by for s in alerts()}
    assert all(g == "resource.label.service_name" for n, g in by_name.items() if n.startswith("Cloud Run"))
    assert [g for n, g in by_name.items() if n.startswith("VPC")] == ["resource.label.connector_name"]


def test_every_alert_is_actionable_documented_and_sustained():
    for spec in alerts(dashboard_url="https://example.com/dash"):
        policy = spec.to_policy()
        assert policy["documentation"]["content"].strip() and policy["documentation"]["mimeType"] == "text/markdown"
        assert spec.duration in {"300s", "600s"}, f"{spec.name}: duración {spec.duration} (una alerta sin ventana es ruido)"
        assert "https://example.com/dash" in policy["documentation"]["content"]


def test_application_errors_alert_requires_sustained_errors_not_a_single_log():
    """Con duration=0 y umbral > 0, un único log ERROR abría y cerraba un incidente."""
    spec = next(s for s in alerts() if "Errores de aplicación" in s.name)
    assert spec.duration == "300s" and spec.threshold == 0 and spec.comparison == "COMPARISON_GT"
    assert "no dispara" in spec.documentation.lower()


def test_cpu_and_memory_tolerate_short_peaks():
    for spec in alerts():
        if "Memoria" in spec.name or "CPU" in spec.name:
            assert spec.duration == "600s"


def test_alert_names_are_unique_have_no_placeholders_and_include_the_project():
    names = [s.name for s in alerts()]
    assert len(set(names)) == len(names)
    assert all("[" not in n and "demo" in n for n in names)


def test_vpc_threshold_is_a_fraction_of_the_real_connector_throughput():
    assert vpc_threshold_bytes(1000, 0.8) == 100_000_000  # 1000 Mbps = 125 MB/s; 80 % = 100 MB/s
    spec = next(s for s in alerts() if s.name.startswith("VPC"))
    assert spec.threshold == 100_000_000 and "1000 Mbps" in spec.documentation
    assert next(s for s in alerts(vpc_connector_max_mbps=100) if s.name.startswith("VPC")).threshold == 10_000_000


def test_projects_without_vpc_connectors_get_no_vpc_alert():
    assert not [s for s in alerts(vpc_connector_max_mbps=None) if s.name.startswith("VPC")]


def test_thresholds_are_configurable():
    config = AlertConfig(project="demo", thresholds=AlertThresholds(latency_p95_ms=1200, memory_utilization=0.9))
    specs = {s.name.split(" — ")[1]: s for s in default_alerts(config)}
    assert specs["Latencia p95 degradada"].threshold == 1200 and specs["Memoria alta"].threshold == 0.9


def test_policies_are_valid_json_for_the_monitoring_api():
    for spec in alerts():
        policy = json.loads(json.dumps(spec.to_policy()))
        assert policy["combiner"] == "OR" and policy["conditions"][0]["conditionThreshold"]["comparison"] == "COMPARISON_GT"


# --- dashboards -------------------------------------------------------------------------------------------------


def tiles(dashboard):
    return dashboard["mosaicLayout"]["tiles"]


@pytest.mark.parametrize("dashboard", [platform_overview(), platform_overview(with_vpc=False), service_detail()])
def test_dashboard_grid_has_no_overlaps_and_fits_12_columns(dashboard):
    cells = set()
    for t in tiles(dashboard):
        assert 0 <= t["xPos"] and t["xPos"] + t["width"] <= 12
        for x in range(t["xPos"], t["xPos"] + t["width"]):
            for y in range(t["yPos"], t["yPos"] + t["height"]):
                assert (x, y) not in cells, f"solapamiento en {(x, y)} ({t['widget']['title']})"
                cells.add((x, y))


def test_platform_overview_uses_aggregates_not_one_line_per_service():
    """Con decenas de servicios, agrupar por servicio superpone decenas de líneas: lo opuesto a una vista general."""
    for node in walk(platform_overview()):
        if "groupByFields" in node:
            assert "resource.label.service_name" not in node["groupByFields"]


def test_platform_overview_labels_the_5xx_tile_as_a_rate_not_a_percentage():
    tile = next(t for t in tiles(platform_overview()) if "5xx" in t["widget"]["title"])
    assert "req/s" in tile["widget"]["title"] and "%" not in tile["widget"]["title"]
    assert "req/s" in tile["widget"]["xyChart"]["thresholds"][0]["label"]


def test_vpc_tile_separates_connectors_with_a_legend():
    tile = next(t for t in tiles(platform_overview()) if "VPC" in t["widget"]["title"])
    for dataset in tile["widget"]["xyChart"]["dataSets"]:
        assert dataset["timeSeriesQuery"]["timeSeriesFilter"]["aggregation"]["groupByFields"] == ["resource.label.connector_name"]
        assert "connector_name" in dataset["legendTemplate"]
    assert not [t for t in tiles(platform_overview(with_vpc=False)) if "VPC" in t["widget"]["title"]]


def test_service_detail_is_one_dashboard_for_any_service_via_template_variable():
    dashboard = service_detail()
    assert dashboard["dashboardFilters"] == [
        {"filterType": "RESOURCE_LABEL", "labelKey": "service_name", "templateVariable": "service_name"}
    ]


def test_series_sharing_the_same_metric_have_distinct_legends():
    """p50/p95/p99 comparten filtro y metric.type: sin legendTemplate serían indistinguibles."""
    tile = next(t for t in tiles(service_detail()) if "Latencia" in t["widget"]["title"])
    legends = [d["legendTemplate"] for d in tile["widget"]["xyChart"]["dataSets"]]
    assert legends == ["p50", "p95", "p99"]


# --- métricas, presupuesto y limpieza -----------------------------------------------------------------------------


def test_log_metrics_target_cloud_run_and_use_the_standard_json_fields():
    metrics = {m.name: m for m in default_metrics()}
    assert set(metrics) == {"application_errors", "unhandled_exceptions", "db_connection_errors"}
    assert all('resource.type="cloud_run_revision"' in m.filter for m in metrics.values())
    assert 'severity="ERROR"' in metrics["application_errors"].filter
    assert "jsonPayload.exception" in metrics["unhandled_exceptions"].filter
    assert "MiBase" in default_metrics("MiBase")[2].filter


def test_budget_amount_is_baseline_times_buffer_rounded_up():
    assert budget_amount(562.60, 1.5) == 850  # 843,9 -> 850
    assert budget_amount(100, 1.0) == 100 and budget_amount(100.01, 1.0) == 110


@pytest.mark.parametrize(("baseline", "buffer"), [(0, 1.5), (-5, 1.5), (100, 0.5)])
def test_budget_refuses_unreasonable_inputs(baseline, buffer):
    with pytest.raises(ValueError):
        budget_amount(baseline, buffer)


def test_budget_command_has_the_standard_thresholds_and_notifies_the_channel():
    args = budget_create_args("ACCT-123", "demo", 850, CHANNEL)
    assert "--budget-amount=850USD" in args and "--filter-projects=projects/demo" in args
    assert [a for a in args if a.startswith("--threshold-rule")] == [
        "--threshold-rule=percent=0.5,basis=current-spend",
        "--threshold-rule=percent=0.9,basis=current-spend",
        "--threshold-rule=percent=1.0,basis=forecasted-spend",  # el 100 % se avisa por PRONÓSTICO, antes de pasarse
    ]
    assert f"--all-updates-rule-monitoring-notification-channels={CHANNEL}" in args


def test_cleanup_policy_keeps_the_last_n_versions():
    assert cleanup_policy(3) == [{"name": "keep-last-3", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}}]
    with pytest.raises(ValueError):
        cleanup_policy(0)


# --- render ------------------------------------------------------------------------------------------------------------


def test_render_writes_valid_json_artifacts(tmp_path):
    written = render_all("demo", CHANNEL, tmp_path, budget_baseline=500)
    names = {p.relative_to(tmp_path).as_posix() for p in written}
    assert {"dashboards/platform-overview.json", "dashboards/service-detail.json", "log-metrics.json",
            "cleanup-policy.json", "budget.json"} <= names
    assert len([n for n in names if n.startswith("policies/")]) == 7
    for path in written:
        json.loads(path.read_text(encoding="utf-8"))
    assert json.loads((tmp_path / "budget.json").read_text(encoding="utf-8"))["amount_usd"] == 750


def test_render_skips_the_budget_without_a_baseline_and_vpc_alert_without_connectors(tmp_path):
    names = {p.name for p in render_all("demo", CHANNEL, tmp_path, vpc_connector_max_mbps=None)}
    assert "budget.json" not in names and not any("vpc" in n for n in names)


def test_slug_is_filesystem_safe():
    assert slug("Cloud Run — Tasa de error 5xx elevada — demo") == "cloud-run-tasa-de-error-5xx-elevada-demo"


# --- aplicación con gcloud (ejecutor falso) ------------------------------------------------------------------------------


class FakeRunner:
    def __init__(self, policies=(), dashboards=(), metrics=(), fail_create_with=None):
        self.calls: list[list[str]] = []
        self.policies, self.dashboards, self.metrics, self.fail = set(policies), set(dashboards), set(metrics), fail_create_with

    @property
    def mutations(self):
        return [c for c in self.calls if c[0] in {"services"} and c[1] == "enable" or "create" in c or "set-cleanup-policies" in c]

    def run(self, args):
        self.calls.append(args)
        if args[:4] == ["beta", "monitoring", "policies", "list"]:
            return 0, "\n".join(self.policies)
        if args[:3] == ["monitoring", "dashboards", "list"]:
            return 0, "\n".join(self.dashboards)
        if args[:3] == ["logging", "metrics", "describe"]:
            return (0, args[3]) if args[3] in self.metrics else (1, "NOT_FOUND")
        if "create" in args and self.fail:
            return 1, self.fail
        return 0, ""


def run_apply(runner, tmp_path, **kwargs):
    return apply_all(runner, "demo", CHANNEL, tmp_path, **kwargs)


def test_apply_creates_everything_on_an_empty_project(tmp_path):
    runner = FakeRunner()
    lines = run_apply(runner, tmp_path)
    creates = [c for c in runner.calls if "create" in c]
    assert len([c for c in creates if c[:3] == ["beta", "monitoring", "policies"]]) == 7
    assert len([c for c in creates if c[:3] == ["monitoring", "dashboards", "create"]]) == 2
    assert len([c for c in creates if c[:3] == ["logging", "metrics", "create"]]) == 3
    assert ["services", "enable", "monitoring.googleapis.com", "logging.googleapis.com", "cloudtrace.googleapis.com",
            "--project=demo"] in runner.calls
    assert all(f"--notification-channels={CHANNEL}" in c for c in creates if c[:3] == ["beta", "monitoring", "policies"])
    assert any("+ política" in line for line in lines)


def test_apply_is_idempotent_and_skips_what_already_exists(tmp_path):
    first = FakeRunner()
    run_apply(first, tmp_path)
    existing = [json.loads(p.read_text(encoding="utf-8"))["displayName"] for p in (tmp_path / "policies").glob("*.json")]
    dashboards = [json.loads(p.read_text(encoding="utf-8"))["displayName"] for p in (tmp_path / "dashboards").glob("*.json")]

    second = FakeRunner(policies=existing, dashboards=dashboards, metrics=[m.name for m in default_metrics()])
    lines = run_apply(second, tmp_path)
    creates = [c for c in second.calls if "create" in c]
    assert creates == [], f"no debía crear nada: {creates}"
    assert sum("ya existía" in line for line in lines) == 7 + 2 + 3


def test_apply_only_creates_the_missing_policy(tmp_path):
    names = [s.name for s in alerts()]
    runner = FakeRunner(policies=names[1:])
    run_apply(runner, tmp_path)
    created = [c for c in runner.calls if c[:4] == ["beta", "monitoring", "policies", "create"]]
    assert len(created) == 1


def test_dry_run_reads_but_never_modifies(tmp_path):
    runner = FakeRunner()
    lines = run_apply(runner, tmp_path, dry_run=True, billing_account="ACCT", budget_baseline=400,
                      ar_repository="repo")
    assert runner.mutations == []  # solo lecturas: listar y describir
    assert runner.calls, "en dry-run se consulta lo existente para mostrar un plan real"
    assert sum(line.startswith("[DRY-RUN]") for line in lines) > 10
    assert any("presupuesto 600 USD/mes" in line for line in lines)


def test_budget_requires_a_real_baseline(tmp_path):
    with pytest.raises(ValueError):
        run_apply(FakeRunner(), tmp_path, billing_account="ACCT", budget_baseline=0)


def test_cleanup_policy_is_a_simulation_unless_explicitly_confirmed(tmp_path):
    """El flag --dry-run de gcloud es 'pegajoso': omitirlo NO aplica la política; hay que pasar --no-dry-run."""
    simulated = FakeRunner()
    run_apply(simulated, tmp_path, ar_repository="repo")
    cleanup = next(c for c in simulated.calls if "set-cleanup-policies" in c)
    assert "--dry-run" in cleanup and "--no-dry-run" not in cleanup

    confirmed = FakeRunner()
    run_apply(confirmed, tmp_path, ar_repository="repo", ar_confirm=True)
    cleanup = next(c for c in confirmed.calls if "set-cleanup-policies" in c)
    assert "--no-dry-run" in cleanup


def test_already_exists_errors_are_tolerated_and_other_errors_reported(tmp_path):
    lines = run_apply(FakeRunner(fail_create_with="ALREADY_EXISTS: Already exists"), tmp_path)
    assert any("ya existía" in line for line in lines) and not any("✗" in line for line in lines)
    lines = run_apply(FakeRunner(fail_create_with="PERMISSION_DENIED: no puede"), tmp_path)
    assert any("✗" in line and "PERMISSION_DENIED" in line for line in lines)


def test_verify_runtime_iam_reports_only_the_missing_roles():
    class Roles:
        def __init__(self, roles):
            self.roles = roles

        def run(self, args):
            return 0, "\n".join(self.roles)

    assert verify_runtime_iam(Roles(RUNTIME_ROLES), "demo", "sa@demo.iam.gserviceaccount.com") == []
    assert verify_runtime_iam(Roles(["roles/logging.logWriter"]), "demo", "sa@x") == [
        "roles/monitoring.metricWriter", "roles/cloudtrace.agent"]


# --- CLI ---------------------------------------------------------------------------------------------------------------------


def test_cli_render_writes_the_files(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["observability", "render", "--project", "demo", "--channel", CHANNEL, "--out", str(out)]) == 0
    assert (out / "dashboards" / "service-detail.json").is_file()
    assert "service-detail.json" in capsys.readouterr().out

