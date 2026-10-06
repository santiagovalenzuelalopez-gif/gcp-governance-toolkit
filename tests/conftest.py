"""Un repositorio de referencia 100 % conforme, generado en un directorio temporal.

Cada test lo ESTROPEA de una en una (quitar un archivo, cambiar una línea) y comprueba que la regla correspondiente
lo detecta: así se prueba que el verificador *falla cuando debe*, no solo que pasa cuando todo está bien."""

from pathlib import Path

import pytest

COMPLIANT: dict[str, str] = {
    "app/__init__.py": "",
    "app/main.py": '''from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import CorrelationMiddleware
from app.routers import chat, health

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(title="demo")
app.add_middleware(CorrelationMiddleware)
app.include_router(health.router)
app.include_router(chat.router, prefix="/api/v1")
''',
    "app/core/__init__.py": "",
    "app/core/config.py": '''from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    service_name: str = "demo"
    service_version: str = "1.0.0"
    environment: str = "dev"
    log_level: str = "INFO"
    gemini_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
''',
    "app/core/logging.py": '''import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"severity": record.levelname, "message": record.getMessage(), "timestamp": "now"}
        payload["logging.googleapis.com/trace"] = "t"
        payload["correlation_id"] = "c"
        return json.dumps(payload)


def setup_logging(level="INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.getLogger().handlers = [handler]
''',
    "app/core/middleware.py": '''import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware

_ctx = ContextVar("ctx", default=None)


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.headers.get("X-Cloud-Trace-Context")
        request.headers.get("traceparent")
        response = await call_next(request)
        print("request_completed")
        response.headers["X-Correlation-ID"] = cid
        return response
''',
    "app/routers/__init__.py": "",
    "app/routers/health.py": '''from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/version")
async def version():
    return {"version": "1"}
''',
    "app/routers/chat.py": '''from fastapi import APIRouter

router = APIRouter()


@router.post("/chat")
async def chat():
    return {}
''',
    "Dockerfile": """FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim
ENV PORT=8080
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /root/.local /home/appuser/.local
COPY app ./app
USER appuser
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
""",
    ".dockerignore": ".git\n.env\n__pycache__\ntests\n",
    ".gitignore": ".env\n__pycache__/\n",
    ".env.example": "SERVICE_NAME=demo\nSERVICE_VERSION=1.0.0\nENVIRONMENT=dev\nLOG_LEVEL=INFO\nGEMINI_API_KEY=\n",
    "requirements.txt": "fastapi==0.115.6\nuvicorn[standard]==0.32.1\npydantic-settings==2.7.1\n",
    "cloudbuild.yaml": """steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args: ['build', '-t', 'us-docker.pkg.dev/$PROJECT_ID/repo/${_SERVICE_NAME}:${COMMIT_SHA}', '.']
  - id: deploy
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args:
      - run
      - deploy
      - ${_SERVICE_NAME}
      - --image=us-docker.pkg.dev/$PROJECT_ID/repo/${_SERVICE_NAME}:${COMMIT_SHA}
      - --no-allow-unauthenticated
      - --service-account=${_RUN_SA}
      - --set-env-vars=ENVIRONMENT=qa,LOG_LEVEL=INFO
      - --set-secrets=GEMINI_API_KEY=GEMINI_API_KEY:latest
      - --min-instances=${_MIN_INSTANCES}
images:
  - 'us-docker.pkg.dev/$PROJECT_ID/repo/${_SERVICE_NAME}:${COMMIT_SHA}'
substitutions:
  _SERVICE_NAME: demo-qa
  _RUN_SA: runtime@proj.iam.gserviceaccount.com
  _MIN_INSTANCES: '0'
timeout: 1200s
""",
    "README.md": """# Servicio demo

Servicio de ejemplo para las pruebas del verificador. Responde preguntas y expone sus endpoints de infraestructura.

## Endpoints

`GET /health` y `GET /version` (sin autenticación), y `POST /api/v1/chat`.

## Variables de entorno

| Variable | Descripción |
|---|---|
| `SERVICE_NAME` | nombre del servicio |
""",
    "tests/test_health.py": 'def test_health(client):\n    assert client.get("/health").status_code == 200\n',
}


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return write_repo(tmp_path / "demo-service", COMPLIANT)


def edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel}: no contiene {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def remove(repo: Path, rel: str) -> None:
    (repo / rel).unlink()
