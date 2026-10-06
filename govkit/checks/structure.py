"""S — Estructura del repositorio: los archivos que el estándar exige."""

from govkit.context import RepoContext
from govkit.report import Severity

APP_FILES = [
    ("main.py", "Punto de entrada (logging + middleware + routers)"),
    ("core/config.py", "Configuración tipada"),
    ("core/logging.py", "Logs JSON compatibles con Cloud Logging"),
    ("core/middleware.py", "Correlation-ID y contexto de traza"),
    ("routers/health.py", "Endpoints /health y /version"),
]
INFRA_FILES = [
    ("Dockerfile", Severity.CRITICAL),
    (".dockerignore", Severity.MEDIUM),
    (".env.example", Severity.MEDIUM),
    ("requirements.txt", Severity.CRITICAL),
]


def run(ctx: RepoContext) -> None:
    if ctx.package is None:
        ctx.fail("S01", Severity.CRITICAL, "No se encontró el paquete de la aplicación (api/, app/ o src/ con main.py)",
                 hint="Crear <paquete>/main.py o declarar 'package' en .govkit.yml")
    else:
        for rel, purpose in APP_FILES:
            path = ctx.pkg(rel)
            ctx.check(ctx.exists(path), "S01", Severity.CRITICAL, f"{path} presente", f"Falta {path}: {purpose}", path)

    for rel, severity in INFRA_FILES:
        ctx.check(ctx.exists(rel), "S02", severity, f"{rel} presente", f"Falta {rel}", rel)

    ctx.check(ctx.exists("cloudbuild.yaml"), "S03", Severity.MEDIUM, "cloudbuild.yaml presente",
              "Falta cloudbuild.yaml (build + deploy como código)", "cloudbuild.yaml",
              "Un servicio sin cloudbuild.yaml no es reproducible: la config de despliegue vive solo en la consola")

    tests_dir = ctx.root / "tests"
    # El test de health puede vivir en cualquier archivo de tests: se busca el endpoint, no un nombre de archivo
    has_health_test = tests_dir.is_dir() and any(
        "/health" in f.read_text(encoding="utf-8", errors="replace") for f in tests_dir.rglob("test_*.py")
    )
    ctx.check(has_health_test, "S04", Severity.MEDIUM, "Existe un test de /health", "No hay ningún test que ejercite /health",
              "tests/", "Mínimo: health, version y propagación de X-Correlation-ID")
