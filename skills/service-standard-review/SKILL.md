---
name: service-standard-review
description: >
  Revisa y corrige un repositorio de microservicio FastAPI/Cloud Run contra un estándar de servicio
  (logging JSON con severity y traza, Correlation-ID, /health y /version, contenedor no-root con $PORT,
  secretos fuera del repo, cloudbuild.yaml privado). Ejecutar desde la raíz del repositorio. Opera en dos
  fases: (1) análisis con `govkit check`, (2) correcciones con confirmación del desarrollador.
  Activar con: "revisa este repo", "verifica cumplimiento", "aplica el estándar", "migra este servicio".
---

# service-standard-review

Skill para Claude Code. El análisis lo hace una herramienta **determinista** (`govkit check`), no el modelo:
así el resultado es reproducible y el mismo que verá el CI. El modelo interpreta el reporte, propone y aplica
correcciones **solo con confirmación**.

## Fase 1 — Análisis (solo lectura)

1. Ejecutar desde la raíz del repositorio:

   ```bash
   govkit check . --format md
   ```

   (Si `govkit` no está instalado: `pip install git+https://github.com/<usuario>/gcp-governance-toolkit`.)

2. Presentar el reporte tal cual y resumirlo: estado, críticos, medios, exceptuados.
3. **No modificar nada** en esta fase.

El reporte nunca imprime valores de credenciales, solo el nombre de la variable. Si aparece `E01`, tratarlo
como una **credencial potencialmente expuesta**: avisar de que hay que rotarla, no solo borrarla del archivo
(el historial de git la conserva).

## Fase 2 — Correcciones (con confirmación)

Preguntar siempre antes: "¿Aplico las correcciones automáticas?". Después, por orden de prioridad:

1. **Credenciales expuestas** (`E01`, `C06`, `D03`): limpiar primero; los secretos van a Secret Manager (`--set-secrets`).
2. **Archivos ausentes** (`S01`, `S02`): crear con la implementación estándar (ver abajo).
3. **Problemas de contenido**: editar de forma quirúrgica; no reescribir archivos enteros por un problema puntual.

Para cada cambio, informar: archivo, qué se cambió y por qué (regla).

### Reglas de oro

- **No inventar datos.** `M01` (documentación sin completar) lo resuelve el desarrollador con datos reales del
  servicio; el modelo puede generar el esqueleto, nunca rellenar proyectos, cuentas, responsables ni endpoints.
- **No tocar** sin confirmación explícita: lógica de negocio en routers, versiones ya fijadas en `requirements.txt`,
  y `cloudbuild.yaml`/pipelines que ya apunten a proyectos reales.
- **Repos existentes (migración)**: antes de mover código de la raíz al paquete, advertir que cambiar rutas puede
  romper un API Gateway ya configurado; proponer una rama de migración y pedir confirmación.
- **Excepciones**: si una regla no aplica de verdad (p. ej. `A04` en un servicio que solo expone un webhook interno),
  se declara en `.govkit.yml` **con un motivo**. Una excepción sin motivo es en sí un incumplimiento crítico (`G01`).
  No añadir excepciones para "hacer pasar" el reporte.

   ```yaml
   waivers:
     - rule: A04
       reason: Webhook interno de Eventarc; no es una API de negocio versionada
   ```

### Implementaciones estándar de referencia

| Archivo | Qué debe hacer |
|---|---|
| `<paquete>/core/logging.py` | `JsonFormatter` a stdout con `severity`, `timestamp`, `message`, `logging.googleapis.com/trace`, `correlation_id`; `setup_logging()` redirige también a uvicorn/gunicorn |
| `<paquete>/core/middleware.py` | `BaseHTTPMiddleware` que propaga o genera `X-Correlation-ID`, parsea `X-Cloud-Trace-Context` y `traceparent`, guarda el contexto en un `ContextVar` y registra `request_completed` |
| `<paquete>/routers/health.py` | `GET /health` y `GET /version` sin prefijo y sin autenticación |
| `<paquete>/main.py` | `setup_logging()` **antes** de `FastAPI()`; `add_middleware`; health sin prefijo; negocio bajo `/api/v1` |
| `Dockerfile` | multi-stage, imagen base con versión fija, usuario no-root, `CMD exec ... --port $PORT` (forma shell) |

Hay una implementación completa de referencia en el repositorio `multitenant-rag-chatbot` (carpeta `app/core/`).

## Verificación posterior

Volver a ejecutar `govkit check . --format md` y presentar el delta:

| Problema original | Estado |
|---|---|
| `A07` middleware sin W3C | ✅ Resuelto |
| `M01` README sin completar | ⏳ Pendiente (requiere datos del desarrollador) |

Terminar indicando el estado final y qué queda pendiente y de quién.
