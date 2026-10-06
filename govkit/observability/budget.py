"""Presupuesto mensual (budget alert) y política de limpieza del registro de imágenes."""

import math

# 50 % y 90 % del gasto real, y 100 % del PRONOSTICADO: avisa antes de pasarse, no después
THRESHOLDS = [(0.5, "current-spend"), (0.9, "current-spend"), (1.0, "forecasted-spend")]


def budget_amount(baseline_monthly_usd: float, buffer: float = 1.5, step: int = 10) -> int:
    """Importe del presupuesto = gasto mensual REAL de referencia x margen, redondeado hacia arriba.

    La referencia debe ser el costo de uso real, no un subtotal con descuentos: un crédito promocional
    temporal que cubre casi todo el uso hace parecer baratísimo un entorno que no lo es, y el presupuesto
    quedaría muy por debajo del gasto cuando el descuento termine. Tampoco un promedio histórico si la
    arquitectura cambió: se usa el último mes completo con el estado ACTUAL. Revisar y bajar el valor
    cuando se retiren los servicios que hoy coexisten (es un estado transitorio, no el definitivo).
    """
    if baseline_monthly_usd <= 0:
        raise ValueError("La referencia de gasto debe ser > 0 (usar el costo de uso real del último mes completo)")
    if buffer < 1:
        raise ValueError("El margen debe ser >= 1")
    return int(math.ceil(baseline_monthly_usd * buffer / step) * step)


def budget_create_args(billing_account: str, project: str, amount_usd: int, channel: str) -> list[str]:
    args = [
        "beta", "billing", "budgets", "create",
        f"--billing-account={billing_account}",
        f"--display-name={project} — Presupuesto mensual",
        f"--budget-amount={amount_usd}USD",
        f"--filter-projects=projects/{project}",
        "--calendar-period=MONTH",
        f"--all-updates-rule-monitoring-notification-channels={channel}",
    ]
    args += [f"--threshold-rule=percent={pct},basis={basis}" for pct, basis in THRESHOLDS]
    return args


def cleanup_policy(keep_last: int = 5) -> list[dict]:
    """Conserva las N versiones más recientes de cada imagen."""
    if keep_last < 1:
        raise ValueError("keep_last debe ser >= 1")
    return [{"name": f"keep-last-{keep_last}", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": keep_last}}]
