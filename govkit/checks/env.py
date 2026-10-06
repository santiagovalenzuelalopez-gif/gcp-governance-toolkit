"""E — Variables de entorno y secretos: .env.example y .gitignore.

Los valores sospechosos NUNCA se imprimen en el reporte (el reporte termina en logs de CI): solo el nombre de la variable."""

import ast
import re

from govkit.context import RepoContext
from govkit.report import Severity

SECRET_KEY_RE = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)", re.IGNORECASE)
# Marcadores aceptados. Convención: un valor de demostración empieza con "demo" (p. ej. demo-token).
PLACEHOLDER_RE = re.compile(r"^(\*+|x{3,}|changeme|change-me|todo|<.*>|your[-_].*|\$\{.*\}|replace.*|example.*|demo.*)$", re.IGNORECASE)
NON_SECRET_VALUE_RE = re.compile(r"^(\d+(\.\d+)?|true|false)$", re.IGNORECASE)  # TOKEN_TTL_SECONDS=3600
# Valores que son credenciales por su FORMA, sea cual sea el nombre de la variable
CREDENTIAL_SHAPES = [
    re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@"),     # esquema://usuario:clave@host
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),                      # clave de API de Google
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(ghp|gho|ghs|glpat)_[A-Za-z0-9_-]{20,}"),     # tokens de GitHub/GitLab
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                         # clave de acceso de AWS
]


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = re.sub(r"\s+#.*$", "", value).strip().strip("\"'")
        values[key.strip()] = value
    return values


def _is_placeholder(value: str) -> bool:
    return value == "" or bool(PLACEHOLDER_RE.match(value))


def _is_suspicious(key: str, value: str) -> bool:
    if _is_placeholder(value) or NON_SECRET_VALUE_RE.match(value):
        return False
    if any(shape.search(value) for shape in CREDENTIAL_SHAPES):
        return True  # parece una credencial por su forma, se llame como se llame la variable
    return bool(SECRET_KEY_RE.search(key))  # una variable de secreto con cualquier valor real


def _settings_vars(ctx: RepoContext) -> set[str]:
    tree = ctx.tree(ctx.pkg("core/config.py")) if ctx.package else None
    if tree is None:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(getattr(b, "id", getattr(b, "attr", "")) == "BaseSettings" for b in node.bases):
            return {n.target.id.upper() for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    return set()


def run(ctx: RepoContext) -> None:
    text = ctx.read(".env.example")
    if text is not None:
        values = parse_env(text)
        suspicious = sorted(key for key, value in values.items() if _is_suspicious(key, value))
        ctx.check(not suspicious, "E01", Severity.CRITICAL, ".env.example sin credenciales reales",
                  f".env.example contiene valores que parecen credenciales en: {', '.join(suspicious)} (valor oculto)",
                  ".env.example", "Dejarlas vacías o con un marcador (*****); la credencial real va en Secret Manager")

        declared = _settings_vars(ctx)
        if declared:
            missing = sorted(declared - set(values))
            ctx.check(not missing, "E02", Severity.MEDIUM, ".env.example documenta toda la configuración",
                      f"Variables de config.py ausentes de .env.example: {', '.join(missing)}", ".env.example")

    gitignore = ctx.read(".gitignore") or ""
    ignored = {line.strip() for line in gitignore.splitlines()}
    ctx.check(bool(ignored & {".env", ".env*", "*.env", ".env.*", "/.env"}), "E03", Severity.CRITICAL,
              ".env está en .gitignore", ".gitignore no excluye .env: un secreto local podría terminar en el repositorio",
              ".gitignore")
