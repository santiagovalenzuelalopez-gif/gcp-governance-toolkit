"""Hallazgos y reporte: modelo común a todas las comprobaciones y sus tres formatos de salida."""

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_ORDER = [Severity.CRITICAL, Severity.MEDIUM, Severity.LOW]
SEVERITY_LABEL = {Severity.CRITICAL: "CRÍTICO", Severity.MEDIUM: "MEDIO", Severity.LOW: "BAJO"}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    path: str | None = None
    hint: str | None = None
    waived_reason: str | None = None  # si no es None, el incumplimiento está exceptuado con justificación


@dataclass(frozen=True)
class Passed:
    rule: str
    message: str


@dataclass
class Report:
    repo: str
    package: str | None
    findings: list[Finding] = field(default_factory=list)
    passed: list[Passed] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.waived_reason is None]

    @property
    def waived(self) -> list[Finding]:
        return [f for f in self.findings if f.waived_reason is not None]

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.failures if f.severity == severity)

    def exit_code(self, fail_on: Severity = Severity.CRITICAL) -> int:
        """1 si hay incumplimientos (sin exceptuar) de la severidad indicada o mayor."""
        threshold = SEVERITY_ORDER.index(fail_on)
        return int(any(SEVERITY_ORDER.index(f.severity) <= threshold for f in self.failures))

    @property
    def status(self) -> str:
        if self.count(Severity.CRITICAL):
            return "NO CUMPLE"
        if self.count(Severity.MEDIUM) or self.count(Severity.LOW):
            return "CUMPLE CON OBSERVACIONES"
        return "CUMPLE"

    # --- formatos ---------------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {
                "repo": self.repo,
                "package": self.package,
                "status": self.status,
                "summary": {s.value: self.count(s) for s in SEVERITY_ORDER}
                | {"passed": len(self.passed), "waived": len(self.waived)},
                "findings": [asdict(f) for f in self.failures],
                "waived": [asdict(f) for f in self.waived],
                "passed": [asdict(p) for p in self.passed],
            },
            ensure_ascii=False,
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"Repositorio: {self.repo}   Paquete: {self.package or '(no detectado)'}", ""]
        for severity in SEVERITY_ORDER:
            for f in (x for x in self.failures if x.severity == severity):
                where = f" [{f.path}]" if f.path else ""
                lines.append(f"  ✗ {f.rule:<4} {SEVERITY_LABEL[severity]:<8} {f.message}{where}")
                if f.hint:
                    lines.append(f"       → {f.hint}")
        for f in self.waived:
            lines.append(f"  ~ {f.rule:<4} EXCEPTUADO {f.message}  (motivo: {f.waived_reason})")
        lines.append("")
        lines.append(
            f"Resumen: {self.count(Severity.CRITICAL)} críticos · {self.count(Severity.MEDIUM)} medios · "
            f"{self.count(Severity.LOW)} bajos · {len(self.waived)} exceptuados · {len(self.passed)} OK"
        )
        lines.append(f"Estado: {self.status}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        out = [f"# Reporte de cumplimiento — `{self.repo}`", "", f"**Estado: {self.status}**", ""]
        out += ["| Severidad | Cantidad |", "|---|---|"]
        out += [f"| {SEVERITY_LABEL[s]} | {self.count(s)} |" for s in SEVERITY_ORDER]
        out += [f"| Exceptuados | {len(self.waived)} |", f"| Verificados OK | {len(self.passed)} |", ""]
        if self.failures:
            out += ["## Incumplimientos", "", "| Regla | Severidad | Hallazgo | Archivo | Corrección sugerida |", "|---|---|---|---|---|"]
            for severity in SEVERITY_ORDER:
                for f in (x for x in self.failures if x.severity == severity):
                    out.append(f"| {f.rule} | {SEVERITY_LABEL[severity]} | {f.message} | `{f.path or '—'}` | {f.hint or '—'} |")
            out.append("")
        if self.waived:
            out += ["## Excepciones justificadas", "", "| Regla | Hallazgo | Motivo |", "|---|---|---|"]
            out += [f"| {f.rule} | {f.message} | {f.waived_reason} |" for f in self.waived]
            out.append("")
        return "\n".join(out)

    def render(self, fmt: str) -> str:
        return {"json": self.to_json, "md": self.to_markdown, "text": self.to_text}[fmt]()
