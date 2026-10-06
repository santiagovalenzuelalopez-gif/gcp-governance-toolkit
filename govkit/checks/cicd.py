"""D — Despliegue: cloudbuild.yaml y pipelines."""

import re
from pathlib import Path

from govkit.checks.container import SECRET_NAME_RE
from govkit.context import RepoContext
from govkit.report import Severity

PIPELINE_GLOBS = ["azure-pipelines*.yml", ".azure-pipelines*.yml", ".github/workflows/*.yml", ".github/workflows/*.yaml"]
STATIC_CREDENTIAL_MARKERS = ("--key-file", "activate-service-account", "credentials.json", "GOOGLE_APPLICATION_CREDENTIALS_JSON")


def _deploy_args(doc) -> list[str]:
    """Argumentos de los pasos que ejecutan `gcloud run deploy`."""
    found = []
    for step in (doc or {}).get("steps", []) or []:
        args = [str(a) for a in step.get("args", []) or []]
        if "deploy" in args and "run" in args:
            found.extend(args)
    return found


def _flag(args: list[str], name: str) -> str | None:
    for arg in args:
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return None


def run(ctx: RepoContext) -> None:
    text = ctx.read("cloudbuild.yaml")
    if text is not None:
        doc = ctx.yaml("cloudbuild.yaml")
        if doc is None:
            return
        images = [str(i) for i in doc.get("images", []) or []]
        ctx.check(bool(images) and all(re.search(r"\$\{?COMMIT_SHA\}?", i) for i in images), "D01", Severity.CRITICAL,
                  "La imagen se etiqueta con COMMIT_SHA", "La imagen no se etiqueta con COMMIT_SHA (despliegues no trazables)",
                  "cloudbuild.yaml", "Un tag mutable (latest) impide saber qué código corre y volver atrás")

        used = set(re.findall(r"\$\{(_[A-Z0-9_]+)\}", text))
        undefined = sorted(used - set(doc.get("substitutions", {}) or {}))
        ctx.check(not undefined, "D01", Severity.MEDIUM, "Substitutions definidas",
                  f"Substitutions usadas sin valor por defecto: {', '.join(undefined)}", "cloudbuild.yaml")
        ctx.check("timeout" in doc, "D01", Severity.MEDIUM, "Timeout explícito",
                  "cloudbuild.yaml no define 'timeout' (se aplica el valor por defecto de la plataforma)", "cloudbuild.yaml")

        args = _deploy_args(doc)
        if args:
            ctx.check("--allow-unauthenticated" not in args, "D02", Severity.CRITICAL, "El servicio no es público",
                      "El despliegue usa --allow-unauthenticated: cualquiera en internet puede invocar el servicio",
                      "cloudbuild.yaml", "Usar --no-allow-unauthenticated y exponer vía API Gateway / balanceador")

            env_vars = _flag(args, "--set-env-vars") or ""
            leaked = sorted({pair.split("=")[0] for pair in env_vars.split(",") if "=" in pair
                             and SECRET_NAME_RE.search(pair.split("=")[0])})
            ctx.check(not leaked, "D03", Severity.CRITICAL, "Secretos fuera de las variables de entorno",
                      f"Posibles secretos como variable de entorno en claro: {', '.join(leaked)}", "cloudbuild.yaml",
                      "--set-secrets=NOMBRE=SECRETO:latest (Secret Manager)")

            sa = _flag(args, "--service-account")
            ctx.check(bool(sa), "D05", Severity.MEDIUM, "Cuenta de servicio de runtime dedicada",
                      "El despliegue no fija --service-account: usaría la cuenta por defecto de Compute (demasiado amplia)",
                      "cloudbuild.yaml")

            minimum = _flag(args, "--min-instances") or ""
            subs = doc.get("substitutions", {}) or {}
            resolved = subs.get(minimum.strip("${}"), minimum) if minimum.startswith("${") else minimum
            ctx.check(str(resolved) in {"", "0"}, "D04", Severity.MEDIUM, "Escala a cero por defecto",
                      f"min-instances={resolved}: una instancia siempre encendida cuesta aunque no haya tráfico",
                      "cloudbuild.yaml", "Solo con una justificación (arranque en frío inaceptable): declararla como excepción")
        else:
            ctx.fail("D02", Severity.MEDIUM, "cloudbuild.yaml no contiene un paso `gcloud run deploy` reconocible",
                     "cloudbuild.yaml", "Sin el paso de despliegue la configuración del servicio no está versionada")

    offenders = []
    for pattern in PIPELINE_GLOBS:
        for path in sorted(Path(ctx.root).glob(pattern)):
            content = path.read_text(encoding="utf-8", errors="replace")
            if any(marker in content for marker in STATIC_CREDENTIAL_MARKERS):
                offenders.append(path.relative_to(ctx.root).as_posix())
    ctx.check(not offenders, "D06", Severity.CRITICAL, "Pipelines sin credenciales estáticas",
              f"Pipelines que autentican con llaves de service account: {', '.join(offenders)}", offenders[0] if offenders else None,
              "Usar Workload Identity Federation (ver cloudrun-wif-cicd-kit)")
