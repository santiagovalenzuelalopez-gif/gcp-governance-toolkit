"""Aplica los artefactos con ``gcloud``: idempotente (consulta antes de crear) y con ``--dry-run``.

El ejecutor de comandos es inyectable: los tests usan uno falso y verifican exactamente qué se habría ejecutado.
"""

import json
import shlex
import subprocess
from pathlib import Path
from typing import Protocol

from govkit.observability.budget import budget_amount, budget_create_args
from govkit.observability.render import render_all

APIS = ["monitoring.googleapis.com", "logging.googleapis.com", "cloudtrace.googleapis.com"]
RUNTIME_ROLES = ["roles/logging.logWriter", "roles/monitoring.metricWriter", "roles/cloudtrace.agent"]


class Runner(Protocol):
    def run(self, args: list[str]) -> tuple[int, str]:
        """Ejecuta ``gcloud <args>`` y devuelve (código de salida, salida estándar+error)."""


class SubprocessRunner:
    def run(self, args: list[str]) -> tuple[int, str]:
        proc = subprocess.run(["gcloud", *args], capture_output=True, text=True, check=False)
        return proc.returncode, (proc.stdout + proc.stderr).replace("\r", "")


def _fmt(args: list[str]) -> str:
    return "gcloud " + " ".join(shlex.quote(a) for a in args)


class _Applier:
    def __init__(self, runner: Runner, project: str, dry_run: bool):
        self.runner, self.project, self.dry_run = runner, project, dry_run
        self.lines: list[str] = []

    def existing(self, args: list[str]) -> set[str]:
        """Lectura (también en dry-run: es de solo lectura y permite mostrar el plan real)."""
        code, out = self.runner.run(args)
        return {line.strip() for line in out.splitlines() if line.strip()} if code == 0 else set()

    def create(self, label: str, args: list[str]) -> None:
        if self.dry_run:
            self.lines.append(f"[DRY-RUN] {label}: {_fmt(args)}")
            return
        code, out = self.runner.run(args)
        if code == 0:
            self.lines.append(f"  + {label}")
        elif "already exists" in out.lower():
            self.lines.append(f"  = {label} ya existía")
        else:
            self.lines.append(f"  ✗ {label}: {out.strip().splitlines()[-1] if out.strip() else 'error'}")


def apply_all(runner: Runner, project: str, channel: str, out: Path, billing_account: str = "",
              budget_baseline: float = 0.0, budget_buffer: float = 1.5, dry_run: bool = False,
              ar_repository: str = "", ar_location: str = "us-central1", ar_confirm: bool = False) -> list[str]:
    render_all(project, channel, out, budget_baseline=budget_baseline, budget_buffer=budget_buffer)
    a = _Applier(runner, project, dry_run)
    p = f"--project={project}"

    a.lines.append("== APIs ==")
    a.create("habilitar APIs", ["services", "enable", *APIS, p])

    a.lines.append("== Métricas basadas en logs ==")
    for metric in json.loads((out / "log-metrics.json").read_text(encoding="utf-8")):
        if a.existing(["logging", "metrics", "describe", metric["name"], p, "--format=value(name)"]):
            a.lines.append(f"  = {metric['name']} ya existía")
            continue
        a.create(f"métrica {metric['name']}", ["logging", "metrics", "create", metric["name"],
                                               f"--description={metric['description']}", f"--log-filter={metric['filter']}", p])

    a.lines.append("== Políticas de alerta ==")
    known = a.existing(["beta", "monitoring", "policies", "list", p, "--format=value(displayName)"])
    for file in sorted((out / "policies").glob("*.json")):
        name = json.loads(file.read_text(encoding="utf-8"))["displayName"]
        if name in known:
            a.lines.append(f"  = {name} ya existía")
            continue
        a.create(f"política {name}", ["beta", "monitoring", "policies", "create", f"--policy-from-file={file}",
                                      f"--notification-channels={channel}", p])

    a.lines.append("== Dashboards ==")
    known = a.existing(["monitoring", "dashboards", "list", p, "--format=value(displayName)"])
    for file in sorted((out / "dashboards").glob("*.json")):
        name = json.loads(file.read_text(encoding="utf-8"))["displayName"]
        if name in known:
            a.lines.append(f"  = {name} ya existía")
            continue
        a.create(f"dashboard {name}", ["monitoring", "dashboards", "create", f"--config-from-file={file}", p])

    if billing_account:
        a.lines.append("== Presupuesto ==")
        amount = budget_amount(budget_baseline, budget_buffer)  # falla si no hay una referencia de gasto real
        a.create(f"presupuesto {amount} USD/mes", budget_create_args(billing_account, project, amount, channel))

    if ar_repository:
        a.lines.append("== Política de limpieza del registro de imágenes ==")
        # --dry-run es "pegajoso": omitirlo NO aplica la política; hay que pasar --no-dry-run de forma explícita.
        # Por eso el valor por defecto es simular, y aplicar de verdad exige confirmación.
        mode = "--no-dry-run" if ar_confirm else "--dry-run"
        a.create(f"limpieza {ar_repository} ({'APLICAR' if ar_confirm else 'simulación'})",
                 ["artifacts", "repositories", "set-cleanup-policies", ar_repository, f"--location={ar_location}", p,
                  f"--policy={out / 'cleanup-policy.json'}", mode])
    return a.lines


def verify_runtime_iam(runner: Runner, project: str, service_account: str) -> list[str]:
    """Roles de observabilidad que FALTAN en la cuenta de runtime. Solo informa: no asigna nada
    (asignar a nivel de proyecto es una decisión de mínimo privilegio que se toma de forma explícita)."""
    code, out = runner.run(["projects", "get-iam-policy", project, "--flatten=bindings[].members",
                            f"--filter=bindings.members:serviceAccount:{service_account}", "--format=value(bindings.role)"])
    assigned = set(out.split()) if code == 0 else set()
    return [role for role in RUNTIME_ROLES if role not in assigned]
