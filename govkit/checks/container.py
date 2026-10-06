"""C — Imagen de contenedor: Dockerfile y .dockerignore."""

import json
import re

from govkit.context import RepoContext
from govkit.report import Severity

SECRET_NAME_RE = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|CREDENTIAL)", re.IGNORECASE)
REQUIRED_IGNORES = {
    ".env": (".env",),
    "__pycache__": ("__pycache__", "*.pyc"),
    "tests": ("tests", "test"),
    ".git": (".git",),
}


def parse_dockerfile(text: str) -> list[tuple[str, str]]:
    """[(INSTRUCCIÓN, argumentos)] con continuaciones de línea (\\) unidas y sin comentarios."""
    joined, buffer = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buffer and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].rstrip() + " "
            continue
        joined.append((buffer + line).strip())
        buffer = ""
    if buffer:
        joined.append(buffer.strip())
    instructions = []
    for entry in joined:
        head, _, rest = entry.partition(" ")
        instructions.append((head.upper(), rest.strip()))
    return instructions


SHELLS = {"sh", "bash", "ash", "dash", "zsh"}


def start_command(arg: str) -> tuple[str, list[str]]:
    """('shell'|'exec'|'exec-shell', [partes]) de un CMD/ENTRYPOINT, como lo interpreta Docker.

    Docker intenta leer el argumento como un array JSON (forma exec); si no es JSON válido lo trata como forma
    SHELL, aunque empiece por '[' (p. ej. `CMD [ "$X" = a ] || ...`). Una forma exec que envuelve un shell
    (`["sh", "-c", "..."]`) sí expande variables, porque las expande ese shell, no Docker."""
    text = arg.strip()
    if text.startswith("["):
        try:
            items = [str(item) for item in json.loads(text)]
        except ValueError:
            return "shell", [text]
        wrapped = bool(items) and items[0].rsplit("/", 1)[-1] in SHELLS and "-c" in items[1:3]
        return ("exec-shell" if wrapped else "exec"), items
    return "shell", [text]


def _final_stage(instructions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    last_from = max((i for i, (op, _) in enumerate(instructions) if op == "FROM"), default=0)
    return instructions[last_from:]


def run(ctx: RepoContext) -> None:
    text = ctx.read("Dockerfile")
    if text is None:
        return  # S02 ya lo reporta
    instructions = parse_dockerfile(text)
    final = _final_stage(instructions)

    users = [arg.split(":")[0].strip() for op, arg in final if op == "USER"]
    ctx.check(bool(users) and users[-1] not in {"root", "0"}, "C01", Severity.CRITICAL,
              "La imagen final corre como usuario no-root", "La imagen final corre como root (falta USER o es root)",
              "Dockerfile", "RUN useradd --create-home --uid 1000 appuser  +  USER appuser")

    stages = [arg for op, arg in instructions if op == "FROM"]
    ctx.check(len(stages) >= 2, "C02", Severity.MEDIUM, "Build multi-stage",
              "Build de una sola etapa: la imagen final arrastra compiladores y cachés", "Dockerfile")

    # Un `ENV PORT=8080` no prueba nada: lo que cuenta es que el COMANDO de arranque use $PORT.
    # Cloud Run inyecta PORT y puede no ser 8080; un puerto fijo en el CMD funciona solo por casualidad.
    port_re = re.compile(r"\$\{?PORT\b")
    forms = [start_command(arg) for op, arg in final if op in {"CMD", "ENTRYPOINT"}]
    uses_port = any(port_re.search(" ".join(parts)) for kind, parts in forms if kind in {"shell", "exec-shell"})
    exec_form_port = any(port_re.search(" ".join(parts)) for kind, parts in forms if kind == "exec")
    ctx.check(uses_port, "C03", Severity.CRITICAL, "El comando de arranque respeta $PORT",
              "La forma exec (JSON) de CMD/ENTRYPOINT NO expande $PORT: el valor llegaría literal"
              if exec_form_port else "El comando de arranque no usa $PORT (Cloud Run decide el puerto)",
              "Dockerfile", "Forma shell: CMD exec gunicorn --bind :$PORT ...")

    stage_names = set()
    unpinned = []
    for ref in stages:
        parts = ref.split()
        image = next((p for p in parts if not p.startswith("--")), "")
        if "AS" in {p.upper() for p in parts}:
            stage_names.add(parts[-1].lower())
        if image.lower() in stage_names or image == "scratch":
            continue
        tag = image.split(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else ""
        if "@sha256:" in image:
            continue
        if tag in {"", "latest"}:
            unpinned.append(image)
    ctx.check(not unpinned, "C04", Severity.MEDIUM, "Imágenes base con versión fija",
              f"Imagen base sin versión fija (builds no reproducibles): {', '.join(unpinned)}", "Dockerfile")

    leaked = [arg.split("=")[0].split()[0] for op, arg in instructions
              if op in {"ENV", "ARG"} and SECRET_NAME_RE.search(arg.split("=")[0])
              and "=" in arg and arg.split("=", 1)[1].strip().strip("\"'") not in {"", "${}"}
              and not arg.split("=", 1)[1].strip().startswith("$")]
    ctx.check(not leaked, "C06", Severity.CRITICAL, "Sin secretos incrustados en la imagen",
              f"El Dockerfile fija valores de posibles secretos: {', '.join(leaked)}", "Dockerfile",
              "Los secretos se inyectan en tiempo de ejecución desde Secret Manager (--set-secrets)")

    ignore = ctx.read(".dockerignore")
    if ignore is not None:
        lines = {line.strip().rstrip("/") for line in ignore.splitlines() if line.strip() and not line.startswith("#")}
        missing = [key for key, forms in REQUIRED_IGNORES.items()
                   if not any(form in lines or f"**/{form}" in lines or f"{form}*" in lines for form in forms)]
        ctx.check(not missing, "C05", Severity.MEDIUM, ".dockerignore completo",
                  f".dockerignore no excluye: {', '.join(missing)}", ".dockerignore",
                  "Un .env o el historial de git dentro de la imagen filtra secretos")
