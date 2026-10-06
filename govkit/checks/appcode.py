"""A — Código de la aplicación: se analiza con el AST de Python, no con expresiones regulares sobre texto,
para que un comentario o una cadena que mencione "severity" no cuente como cumplimiento."""

import ast
import re

from govkit.context import RepoContext
from govkit.report import Severity

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route"}
VERSION_PREFIX = r"^/(api/)?v\d+"


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _calls(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _strings(tree: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _route_paths(tree: ast.AST) -> set[str]:
    """Rutas declaradas con decoradores tipo @router.get("/x")."""
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _name(dec.func) in ROUTE_METHODS and dec.args:
                    arg = dec.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        paths.add(arg.value)
    return paths


def _check_main(ctx: RepoContext) -> None:
    path = ctx.pkg("main.py")
    tree = ctx.tree(path)
    if tree is None:
        return
    calls = _calls(tree)

    setup = min((c.lineno for c in calls if _name(c.func) == "setup_logging"), default=None)
    app = min((c.lineno for c in calls if _name(c.func) == "FastAPI"), default=None)
    ctx.check(setup is not None and (app is None or setup < app), "A01", Severity.CRITICAL,
              "setup_logging() se llama antes de crear la app",
              "setup_logging() no se llama, o se llama después de crear FastAPI()", path,
              "Sin esto los logs de arranque y los de uvicorn salen sin formato JSON")

    has_middleware = any(
        _name(c.func) == "add_middleware" and c.args and "correlation" in _name(c.args[0]).lower() for c in calls
    )
    ctx.check(has_middleware, "A02", Severity.CRITICAL, "El middleware de correlación está registrado",
              "No se registra un middleware de correlación (app.add_middleware(CorrelationMiddleware))", path,
              "Sin él no hay X-Correlation-ID ni contexto de traza en los logs")

    includes = [c for c in calls if _name(c.func) == "include_router"]
    health_mount = [c for c in includes if c.args and "health" in ast.unparse(c.args[0])]
    health_prefixed = any(k.arg == "prefix" for c in health_mount for k in c.keywords)
    ctx.check(bool(health_mount) and not health_prefixed, "A03", Severity.CRITICAL,
              "El router de health se monta sin prefijo",
              "El router de health no se monta, o se monta bajo un prefijo", path,
              "/health y /version son endpoints de infraestructura: sin /v1 y sin autenticación")

    versioned = any(
        k.arg == "prefix" and isinstance(k.value, ast.Constant) and _is_versioned(str(k.value.value))
        for c in includes for k in c.keywords
    )
    versioned = versioned or ctx.exists(ctx.pkg("routers/v1")) or _routers_declare_version(ctx)
    ctx.check(versioned, "A04", Severity.MEDIUM, "Los endpoints de negocio están versionados (/v1)",
              "No hay endpoints de negocio versionados (include_router(..., prefix='/api/v1') o routers/v1/)", path,
              "Si el servicio solo expone un webhook interno, declarar una excepción justificada en .govkit.yml")

    inline_routes = _route_paths(tree) - {"/health", "/version"}
    ctx.check(not inline_routes, "A08", Severity.MEDIUM, "main.py no contiene endpoints de negocio",
              f"main.py define endpoints directamente: {', '.join(sorted(inline_routes))}", path,
              "Mover los endpoints a un router: main.py solo hace composición")


def _routers_declare_version(ctx: RepoContext) -> bool:
    """La versión puede estar en APIRouter(prefix='/api/v1/...') o en la ruta del propio decorador."""
    routers = ctx.root / ctx.pkg("routers")
    if not routers.is_dir():
        return False
    for file in routers.rglob("*.py"):
        tree = ctx.tree(file.relative_to(ctx.root).as_posix())
        if tree is None:
            continue
        for call in _calls(tree):
            if _name(call.func) == "APIRouter":
                for kw in call.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and _is_versioned(str(kw.value.value)):
                        return True
        if any(_is_versioned(path) for path in _route_paths(tree)):
            return True
    return False


def _is_versioned(prefix: str) -> bool:
    return bool(re.match(VERSION_PREFIX, prefix))


def _check_config(ctx: RepoContext) -> None:
    path = ctx.pkg("core/config.py")
    tree = ctx.tree(path)
    if tree is None:
        return
    settings = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                     and any(_name(b) == "BaseSettings" for b in n.bases)), None)
    ctx.check(settings is not None, "A05", Severity.CRITICAL, "La configuración usa BaseSettings",
              "La configuración no usa pydantic BaseSettings", path)
    if settings is not None:
        fields = {n.target.id for n in settings.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
        missing = {"service_name", "service_version", "environment", "log_level"} - fields
        ctx.check(not missing, "A05", Severity.CRITICAL, "Declara las variables base",
                  f"Faltan variables base de configuración: {', '.join(sorted(missing))}", path)

    getter = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_settings"), None)
    cached = getter is not None and any(_name(d.func if isinstance(d, ast.Call) else d) in {"lru_cache", "cache"}
                                        for d in getter.decorator_list)
    ctx.check(cached, "A05", Severity.MEDIUM, "get_settings() está cacheado",
              "get_settings() no está cacheado (@lru_cache): se relee el entorno en cada request", path)


def _check_logging(ctx: RepoContext) -> None:
    path = ctx.pkg("core/logging.py")
    tree = ctx.tree(path)
    if tree is None:
        return
    strings = _strings(tree)
    required = {
        "severity": "campo 'severity' (Cloud Logging no clasifica la gravedad con 'level')",
        "timestamp": "campo 'timestamp'",
        "logging.googleapis.com/trace": "'logging.googleapis.com/trace' para correlacionar con Cloud Trace",
        "correlation_id": "'correlation_id' en cada línea",
    }
    for key, why in required.items():
        ctx.check(key in strings, "A06", Severity.CRITICAL, f"logging.py emite {key}", f"logging.py no emite {why}", path)
    has_dumps = any(_name(c.func) == "dumps" for c in _calls(tree))
    has_setup = any(isinstance(n, ast.FunctionDef) and n.name == "setup_logging" for n in ast.walk(tree))
    ctx.check(has_dumps and has_setup, "A06", Severity.CRITICAL, "Logs JSON con setup_logging()",
              "logging.py no serializa a JSON (json.dumps) o no define setup_logging()", path)


def _check_middleware(ctx: RepoContext) -> None:
    path = ctx.pkg("core/middleware.py")
    tree = ctx.tree(path)
    if tree is None:
        return
    strings = _strings(tree)
    is_http = any(isinstance(n, ast.ClassDef) and any(_name(b) == "BaseHTTPMiddleware" for b in n.bases)
                  for n in ast.walk(tree))
    ctx.check(is_http, "A07", Severity.CRITICAL, "Middleware basado en BaseHTTPMiddleware",
              "No hay una clase que herede de BaseHTTPMiddleware", path)
    for header, why in {
        "X-Correlation-ID": "genera y propaga el id de correlación",
        "X-Cloud-Trace-Context": "parsea el contexto de traza de GCP",
        "traceparent": "parsea el contexto de traza W3C",
        "request_completed": "registra un log por request (método, path, status, duración)",
    }.items():
        ctx.check(header in strings, "A07", Severity.CRITICAL, f"middleware.py: {header}",
                  f"middleware.py no usa '{header}' ({why})", path)
    names = {_name(c.func) for c in _calls(tree)}
    ctx.check("ContextVar" in names, "A07", Severity.CRITICAL, "El contexto va en un ContextVar",
              "El contexto de traza no se guarda en un ContextVar (se mezclaría entre requests concurrentes)", path)
    ctx.check("uuid4" in names, "A07", Severity.MEDIUM, "Genera uuid4 si no llega X-Correlation-ID",
              "No genera un uuid4 cuando el request no trae X-Correlation-ID", path)


def _check_health(ctx: RepoContext) -> None:
    path = ctx.pkg("routers/health.py")
    tree = ctx.tree(path)
    if tree is None:
        return
    paths = _route_paths(tree)
    for route in ("/health", "/version"):
        ctx.check(route in paths, "A03", Severity.CRITICAL, f"Define GET {route}", f"health.py no define {route}", path)


def run(ctx: RepoContext) -> None:
    if ctx.package is None:
        return
    _check_main(ctx)
    _check_config(ctx)
    _check_logging(ctx)
    _check_middleware(ctx)
    _check_health(ctx)
