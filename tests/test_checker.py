import json

import pytest

from govkit.checks import check_repo
from govkit.cli import main
from govkit.report import Severity
from tests.conftest import COMPLIANT, edit, remove, write_repo


def rules(report, severity=None):
    return {f.rule for f in report.failures if severity is None or f.severity == severity}


def test_reference_repo_is_fully_compliant(repo):
    report = check_repo(repo)
    assert report.failures == [] and report.status == "CUMPLE"
    assert report.exit_code() == 0 and len(report.passed) > 40


# Cada caso estropea UNA cosa del repo conforme y espera que se dispare la regla indicada
BREAKS = [
    # id, (archivo, texto a reemplazar, reemplazo) | "remove:<archivo>", regla, severidad
    ("sin middleware", "remove:app/core/middleware.py", "S01", Severity.CRITICAL),
    ("sin Dockerfile", "remove:Dockerfile", "S02", Severity.CRITICAL),
    ("sin cloudbuild", "remove:cloudbuild.yaml", "S03", Severity.MEDIUM),
    ("sin test de health", ("tests/test_health.py", "/health", "/otra"), "S04", Severity.MEDIUM),
    ("logging después de la app", ("app/main.py", "setup_logging(settings.log_level)\n\napp = FastAPI(title=\"demo\")",
                                   "app = FastAPI(title=\"demo\")\nsetup_logging(settings.log_level)"), "A01", Severity.CRITICAL),
    ("sin middleware registrado", ("app/main.py", "app.add_middleware(CorrelationMiddleware)\n", ""), "A02", Severity.CRITICAL),
    ("health bajo prefijo", ("app/main.py", "include_router(health.router)", "include_router(health.router, prefix='/api')"), "A03", Severity.CRITICAL),
    ("health sin /version", ("app/routers/health.py", '"/version"', '"/otra"'), "A03", Severity.CRITICAL),
    ("negocio sin versión", ("app/main.py", 'prefix="/api/v1"', 'prefix="/chat-api"'), "A04", Severity.MEDIUM),
    ("sin BaseSettings", ("app/core/config.py", "(BaseSettings)", "()"), "A05", Severity.CRITICAL),
    ("settings sin caché", ("app/core/config.py", "@lru_cache\n", ""), "A05", Severity.MEDIUM),
    ("sin variable base", ("app/core/config.py", '    log_level: str = "INFO"\n', ""), "A05", Severity.CRITICAL),
    ("logging sin severity", ("app/core/logging.py", '"severity"', '"level"'), "A06", Severity.CRITICAL),
    ("logging sin traza", ("app/core/logging.py", "logging.googleapis.com/trace", "otro"), "A06", Severity.CRITICAL),
    ("middleware sin W3C", ("app/core/middleware.py", '"traceparent"', '"x"'), "A07", Severity.CRITICAL),
    ("middleware sin ContextVar", ("app/core/middleware.py", "ContextVar(", "dict("), "A07", Severity.CRITICAL),
    ("endpoint en main", ("app/main.py", "app.include_router(health.router)", '@app.get("/quien")\ndef q(): ...\napp.include_router(health.router)'), "A08", Severity.MEDIUM),
    ("Docker como root", ("Dockerfile", "USER appuser\n", ""), "C01", Severity.CRITICAL),
    ("Docker sin $PORT", ("Dockerfile", "--port ${PORT}", "--port 8080"), "C03", Severity.CRITICAL),
    ("imagen latest", ("Dockerfile", "FROM python:3.11-slim\nENV", "FROM python:latest\nENV"), "C04", Severity.MEDIUM),
    ("secreto en Dockerfile", ("Dockerfile", "ENV PORT=8080", "ENV PORT=8080\nENV API_KEY=abc123"), "C06", Severity.CRITICAL),
    ("dockerignore incompleto", (".dockerignore", ".env\n", ""), "C05", Severity.MEDIUM),
    ("credencial en .env.example", (".env.example", "GEMINI_API_KEY=", "GEMINI_API_KEY=" + "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"), "E01", Severity.CRITICAL),
    ("URL con clave en .env.example", (".env.example", "GEMINI_API_KEY=", "DB_URL=mysql://root:hunter2@db:3306/app\nGEMINI_API_KEY="), "E01", Severity.CRITICAL),
    ("variable sin documentar", (".env.example", "GEMINI_API_KEY=\n", ""), "E02", Severity.MEDIUM),
    ("sin .env en gitignore", (".gitignore", ".env\n", ""), "E03", Severity.CRITICAL),
    ("imagen sin COMMIT_SHA", ("cloudbuild.yaml", "images:\n  - 'us-docker.pkg.dev/$PROJECT_ID/repo/${_SERVICE_NAME}:${COMMIT_SHA}'",
                               "images:\n  - 'us-docker.pkg.dev/$PROJECT_ID/repo/${_SERVICE_NAME}:latest'"), "D01", Severity.CRITICAL),
    ("substitution sin definir", ("cloudbuild.yaml", "  _RUN_SA: runtime@proj.iam.gserviceaccount.com\n", ""), "D01", Severity.MEDIUM),
    ("servicio público", ("cloudbuild.yaml", "--no-allow-unauthenticated", "--allow-unauthenticated"), "D02", Severity.CRITICAL),
    ("secreto en env vars", ("cloudbuild.yaml", "ENVIRONMENT=qa,LOG_LEVEL=INFO", "ENVIRONMENT=qa,DB_PASSWORD=x1"), "D03", Severity.CRITICAL),
    ("sin cuenta de runtime", ("cloudbuild.yaml", "      - --service-account=${_RUN_SA}\n", ""), "D05", Severity.MEDIUM),
    ("min-instances 1", ("cloudbuild.yaml", "_MIN_INSTANCES: '0'", "_MIN_INSTANCES: '1'"), "D04", Severity.MEDIUM),
    ("README plantilla", ("README.md", "Servicio demo", "TODO completar. Servicio demo"), "M01", Severity.MEDIUM),
    ("README sin variables", ("README.md", "## Variables de entorno", "## Otra cosa"), "M02", Severity.MEDIUM),
    ("dependencia sin versión", ("requirements.txt", "uvicorn[standard]==0.32.1", "uvicorn[standard]"), "P02", Severity.MEDIUM),
    ("pytest en producción", ("requirements.txt", "fastapi==0.115.6\n", "fastapi==0.115.6\npytest==8.3.4\n"), "P03", Severity.MEDIUM),
    ("sin uvicorn", ("requirements.txt", "uvicorn[standard]==0.32.1\n", ""), "P01", Severity.CRITICAL),
    ("Python inválido", ("app/main.py", "app = FastAPI", "app = = FastAPI"), "S00", Severity.CRITICAL),
]


@pytest.mark.parametrize(("name", "change", "rule", "severity"), BREAKS, ids=[b[0] for b in BREAKS])
def test_each_rule_catches_its_violation(repo, name, change, rule, severity):
    if isinstance(change, str):
        remove(repo, change.split(":", 1)[1])
    else:
        edit(repo, *change)
    report = check_repo(repo)
    assert rule in rules(report, severity), f"{rule} ({severity}) no se disparó; hallazgos: {sorted(rules(report))}"
    assert (report.exit_code() == 1) == (severity == Severity.CRITICAL)


def test_exec_form_cmd_does_not_expand_port(repo):
    """CMD ["uvicorn", "--port", "$PORT"] pasa el texto literal '$PORT': no hay shell que lo expanda."""
    edit(repo, "Dockerfile", "CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}",
         'CMD ["uvicorn", "app.main:app", "--port", "$PORT"]')
    assert any("exec" in f.message for f in check_repo(repo).failures if f.rule == "C03")


def test_exec_form_wrapping_a_shell_does_expand_port(repo):
    edit(repo, "Dockerfile", "CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}",
         'CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]')
    assert "C03" not in rules(check_repo(repo))


def test_cmd_starting_with_a_bracket_that_is_not_json_is_shell_form_as_docker_reads_it(repo):
    """`CMD [ "$X" = a ] || ...; exec ...` no es JSON: Docker lo trata como forma shell (y funciona)."""
    edit(repo, "Dockerfile", "CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}",
         'CMD [ "$MODE" = "prod" ] || python seed.py; exec uvicorn app.main:app --port ${PORT}')
    assert "C03" not in rules(check_repo(repo))


def test_env_port_alone_does_not_prove_the_command_uses_it(repo):
    edit(repo, "Dockerfile", "--port ${PORT}", "--port 8080")
    assert "C03" in rules(check_repo(repo), Severity.CRITICAL)


def test_single_stage_dockerfile_is_flagged(repo):
    (repo / "Dockerfile").write_text("FROM python:3.11-slim\nENV PORT=8080\nUSER appuser\nCMD exec uvicorn a:b --port ${PORT}\n", encoding="utf-8")
    assert "C02" in rules(check_repo(repo), Severity.MEDIUM)


def test_a_comment_is_not_code(repo):
    """El análisis es sobre el AST: un comentario no sustituye al código. (Límite documentado: un docstring o una
    cadena literal sí cuentan como cadenas del módulo; la regla comprueba que el formatter las use, no que las ejecute.)"""
    (repo / "app/core/logging.py").write_text(
        "import json\n\n# severity timestamp logging.googleapis.com/trace correlation_id\n"
        "def setup_logging(level='INFO'):\n    return json.dumps({})\n", encoding="utf-8")
    failed = {f.message for f in check_repo(repo).failures if f.rule == "A06"}
    assert len(failed) == 4  # severity, timestamp, trace, correlation_id


def test_credential_values_are_never_printed_in_any_format(repo):
    secret = "hunter2-super-secreto"
    edit(repo, ".env.example", "GEMINI_API_KEY=", f"DB_URL=mysql://root:{secret}@db:3306/app\nGEMINI_API_KEY=")
    report = check_repo(repo)
    assert "E01" in rules(report)
    for fmt in ("text", "json", "md"):
        assert secret not in report.render(fmt), f"el valor sensible apareció en el formato {fmt}"
    assert "DB_URL" in report.render("text")  # sí se nombra la variable


@pytest.mark.parametrize("value", ["", "*****", "demo-token", "<tu-clave>", "changeme", "3600", "true"])
def test_placeholders_and_non_secret_values_are_not_flagged(repo, value):
    edit(repo, ".env.example", "GEMINI_API_KEY=", f"GEMINI_API_KEY={value}")
    assert "E01" not in rules(check_repo(repo))


def test_numeric_token_setting_is_not_a_false_positive(repo):
    edit(repo, ".env.example", "GEMINI_API_KEY=", "TOKEN_TTL_SECONDS=3600\nGEMINI_API_KEY=")
    assert "E01" not in rules(check_repo(repo))


def test_plain_urls_without_credentials_are_fine(repo):
    edit(repo, ".env.example", "GEMINI_API_KEY=", "TICKETS_API_URL=https://api.example.com/v1\nGEMINI_API_KEY=")
    assert "E01" not in rules(check_repo(repo))


def test_static_credentials_in_pipelines_are_flagged(repo):
    write_repo(repo, {"azure-pipelines.yml": "steps:\n  - bash: gcloud auth activate-service-account --key-file=k.json\n"})
    report = check_repo(repo)
    assert "D06" in rules(report, Severity.CRITICAL)


def test_spanish_word_todo_is_not_a_placeholder(repo):
    edit(repo, "README.md", "Servicio demo", "Servicio demo. Hace todo lo necesario con todo cuidado")
    assert "M01" not in rules(check_repo(repo))


# --- paquetes y excepciones ----------------------------------------------------------------------------


def test_api_package_layout_is_detected(tmp_path):
    files = {rel.replace("app/", "api/", 1): content.replace("app.", "api.") for rel, content in COMPLIANT.items()}
    repo = write_repo(tmp_path / "svc", files)
    report = check_repo(repo)
    assert report.package == "api" and report.failures == []


def test_missing_package_is_a_critical_finding(tmp_path):
    repo = write_repo(tmp_path / "svc", {"README.md": "x"})
    assert "S01" in rules(check_repo(repo), Severity.CRITICAL)


def test_waiver_with_reason_moves_the_finding_out_of_the_failures(repo):
    edit(repo, "app/main.py", 'prefix="/api/v1"', 'prefix="/webhook"')
    (repo / ".govkit.yml").write_text("waivers:\n  - rule: A04\n    reason: Webhook interno, no es una API versionada\n", encoding="utf-8")
    report = check_repo(repo)
    assert "A04" not in rules(report)
    assert [w.rule for w in report.waived] == ["A04"] and "Webhook interno" in report.waived[0].waived_reason
    assert report.exit_code() == 0 and "EXCEPTUADO" in report.to_text()


def test_waiver_without_reason_is_itself_a_critical_finding(repo):
    edit(repo, "app/main.py", 'prefix="/api/v1"', 'prefix="/webhook"')
    (repo / ".govkit.yml").write_text("waivers:\n  - rule: A04\n", encoding="utf-8")
    report = check_repo(repo)
    assert "G01" in rules(report, Severity.CRITICAL) and "A04" in rules(report)  # y la excepción no se aplica


def test_waiver_only_covers_the_declared_rule(repo):
    edit(repo, "Dockerfile", "USER appuser\n", "")
    (repo / ".govkit.yml").write_text("waivers:\n  - rule: A04\n    reason: x\n", encoding="utf-8")
    assert "C01" in rules(check_repo(repo))


# --- formatos y CLI -------------------------------------------------------------------------------------------


def test_json_markdown_and_text_formats(repo):
    edit(repo, "Dockerfile", "USER appuser\n", "")
    report = check_repo(repo)
    data = json.loads(report.to_json())
    assert data["status"] == "NO CUMPLE" and data["summary"]["critical"] == 1
    assert data["findings"][0]["rule"] == "C01" and "passed" in data
    md = report.to_markdown()
    assert md.startswith("# Reporte de cumplimiento") and "| C01 |" in md
    assert "✗ C01" in report.to_text()


def test_cli_exit_codes_and_fail_on(repo, capsys):
    assert main(["check", str(repo)]) == 0
    edit(repo, "README.md", "## Variables de entorno", "## Otra cosa")  # solo un hallazgo medio
    assert main(["check", str(repo)]) == 0  # por defecto solo fallan los críticos
    assert main(["check", str(repo), "--fail-on", "medium"]) == 1
    capsys.readouterr()
    edit(repo, "Dockerfile", "USER appuser\n", "")
    assert main(["check", str(repo), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NO CUMPLE"


def test_checker_never_modifies_the_repository(repo):
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    check_repo(repo)
    after = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    assert before == after
