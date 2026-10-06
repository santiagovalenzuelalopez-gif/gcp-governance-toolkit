"""M, P — Documentación y dependencias."""

import re

from govkit.context import RepoContext
from govkit.report import Severity

# Marcadores de plantilla sin completar. TODO/FIXME/XXX distinguen mayúsculas: "todo" es una palabra normal en español.
PLACEHOLDER_RE = re.compile(r"(\bTODO\b|\bFIXME\b|\bXXX\b|\[COMPLETAR[^\]]*\]|<COMPLETAR[^>]*>|lorem ipsum)")
ENV_SECTION_RE = re.compile(r"^#{1,6}\s.*(variables? de entorno|environment variables)", re.IGNORECASE | re.MULTILINE)
REQUIRED_PACKAGES = {"fastapi", "uvicorn", "pydantic-settings"}
DEV_ONLY = {"pytest", "pytest-asyncio", "pytest-cov", "ruff", "black", "flake8", "mypy", "coverage"}
PIN_RE = re.compile(r"(==|~=|>=|<=|<|>|!=)")


def _doc(ctx: RepoContext) -> tuple[str | None, str | None]:
    for rel in ("docs/MANUAL.md", "README.md"):
        text = ctx.read(rel)
        if text is not None:
            return rel, text
    return None, None


def run_docs(ctx: RepoContext) -> None:
    rel, text = _doc(ctx)
    if text is None:
        ctx.fail("M01", Severity.MEDIUM, "No hay documentación (docs/MANUAL.md o README.md)", "README.md")
        return
    placeholders = sorted({m.group(0).strip() for m in PLACEHOLDER_RE.finditer(text)})
    ctx.check(not placeholders and len(text.strip()) > 200, "M01", Severity.MEDIUM, f"{rel} completado",
              f"{rel} es una plantilla sin completar (marcadores: {', '.join(placeholders) or 'casi vacío'})", rel,
              "La documentación se completa con datos reales del servicio; nunca se inventan")
    ctx.check(bool(ENV_SECTION_RE.search(text)), "M02", Severity.MEDIUM, f"{rel} documenta las variables de entorno",
              f"{rel} no tiene una sección de variables de entorno", rel)
    ctx.check("/health" in text, "M03", Severity.LOW, f"{rel} menciona /health",
              f"{rel} no documenta los endpoints de infraestructura (/health, /version)", rel)


def _parse_requirements(text: str) -> list[tuple[str, str]]:
    packages = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-r", "-c", "-e", "--")):
            continue
        name = re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].strip().lower().replace("_", "-")
        packages.append((name, line))
    return packages


def run_deps(ctx: RepoContext) -> None:
    text = ctx.read("requirements.txt")
    if text is None:
        return
    packages = _parse_requirements(text)
    names = {n for n, _ in packages}

    missing = sorted(REQUIRED_PACKAGES - names)
    ctx.check(not missing, "P01", Severity.CRITICAL, "Dependencias base presentes",
              f"requirements.txt no incluye: {', '.join(missing)}", "requirements.txt")

    unpinned = sorted(n for n, line in packages if not PIN_RE.search(line))
    ctx.check(not unpinned, "P02", Severity.MEDIUM, "Dependencias con versión",
              f"Dependencias sin versión (builds no reproducibles): {', '.join(unpinned)}", "requirements.txt")

    dev = sorted(names & DEV_ONLY)
    ctx.check(not dev, "P03", Severity.MEDIUM, "Sin dependencias de desarrollo en producción",
              f"Dependencias de desarrollo en requirements.txt: {', '.join(dev)}", "requirements.txt",
              "Moverlas a requirements-dev.txt: la imagen de producción no las necesita")
