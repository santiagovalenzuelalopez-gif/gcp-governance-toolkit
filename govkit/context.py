"""Contexto de un repositorio bajo revisión: lectura de archivos, AST con caché y registro de resultados."""

import ast
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from govkit.report import Finding, Passed, Report, Severity

PACKAGE_CANDIDATES = ("api", "app", "src")
CONFIG_FILE = ".govkit.yml"


@dataclass
class Waiver:
    rule: str
    reason: str


@dataclass
class RepoContext:
    root: Path
    package: str | None = None
    waivers: dict[str, Waiver] = field(default_factory=dict)
    report: Report = field(init=False)
    _trees: dict[str, ast.Module | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.report = Report(repo=self.root.name, package=self.package)

    # --- archivos ---------------------------------------------------------------------------

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def read(self, rel: str) -> str | None:
        path = self.root / rel
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None

    def pkg(self, rel: str) -> str:
        """Ruta relativa dentro del paquete de la aplicación (p. ej. 'core/logging.py')."""
        return f"{self.package}/{rel}" if self.package else rel

    def tree(self, rel: str) -> ast.Module | None:
        """AST del archivo (None si no existe o no es Python válido; esto último ya es un hallazgo)."""
        if rel not in self._trees:
            source = self.read(rel)
            tree = None
            if source is not None:
                try:
                    tree = ast.parse(source)
                except SyntaxError as exc:
                    self.fail("S00", Severity.CRITICAL, f"No es Python válido: {exc.msg} (línea {exc.lineno})", rel)
            self._trees[rel] = tree
        return self._trees[rel]

    def yaml(self, rel: str):
        text = self.read(rel)
        if text is None:
            return None
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self.fail("S00", Severity.CRITICAL, f"YAML inválido: {str(exc).splitlines()[0]}", rel)
            return None

    # --- resultados -------------------------------------------------------------------------

    def ok(self, rule: str, message: str) -> None:
        self.report.passed.append(Passed(rule, message))

    def fail(self, rule: str, severity: Severity, message: str, path: str | None = None, hint: str | None = None) -> None:
        waiver = self.waivers.get(rule)
        self.report.findings.append(
            Finding(rule, severity, message, path, hint, waived_reason=waiver.reason if waiver else None)
        )

    def check(self, condition: bool, rule: str, severity: Severity, ok_msg: str, fail_msg: str,
              path: str | None = None, hint: str | None = None) -> bool:
        if condition:
            self.ok(rule, ok_msg)
        else:
            self.fail(rule, severity, fail_msg, path, hint)
        return condition


def detect_package(root: Path) -> str | None:
    for name in PACKAGE_CANDIDATES:
        if (root / name / "main.py").is_file():
            return name
    return None


def load_context(root: Path, package: str | None = None) -> RepoContext:
    """Crea el contexto leyendo ``.govkit.yml`` (paquete y excepciones justificadas)."""
    config = {}
    config_path = root / CONFIG_FILE
    if config_path.is_file():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ctx = RepoContext(root=root, package=package or config.get("package") or detect_package(root))

    for entry in config.get("waivers", []) or []:
        rule, reason = str(entry.get("rule", "")).strip(), str(entry.get("reason", "") or "").strip()
        if not rule:
            continue
        if not reason:
            # Una excepción sin justificación no es una excepción: es un incumplimiento oculto.
            ctx.report.findings.append(
                Finding("G01", Severity.CRITICAL, f"La excepción de {rule} no tiene justificación", CONFIG_FILE,
                        "Cada excepción debe explicar por qué el servicio no cumple la regla")
            )
            continue
        ctx.waivers[rule] = Waiver(rule, reason)
    return ctx
