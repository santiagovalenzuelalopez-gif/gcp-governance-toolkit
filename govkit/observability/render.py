"""Escribe los artefactos de observabilidad (JSON) a un directorio. No toca ninguna nube."""

import json
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path

from govkit.observability.alerts import AlertConfig, default_alerts
from govkit.observability.budget import budget_amount, cleanup_policy
from govkit.observability.dashboards import platform_overview, service_detail
from govkit.observability.metrics import default_metrics


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def render_all(project: str, channel: str, out: Path, dashboard_url: str = "", budget_baseline: float = 0.0,
               budget_buffer: float = 1.5, vpc_connector_max_mbps: float | None = 1000) -> list[Path]:
    config = AlertConfig(project=project, dashboard_url=dashboard_url, vpc_connector_max_mbps=vpc_connector_max_mbps)
    written = []
    for spec in default_alerts(config):
        written.append(_write(out / "policies" / f"{slug(spec.name)}.json", spec.to_policy()))
    written.append(_write(out / "dashboards" / "platform-overview.json",
                          platform_overview(with_vpc=bool(vpc_connector_max_mbps))))
    written.append(_write(out / "dashboards" / "service-detail.json", service_detail()))
    written.append(_write(out / "log-metrics.json", [asdict(m) for m in default_metrics()]))
    written.append(_write(out / "cleanup-policy.json", cleanup_policy()))
    if budget_baseline > 0:
        written.append(_write(out / "budget.json", {"project": project, "channel": channel,
                                                    "amount_usd": budget_amount(budget_baseline, budget_buffer)}))
    return written
