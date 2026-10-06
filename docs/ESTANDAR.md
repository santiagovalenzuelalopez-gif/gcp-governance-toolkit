# Catálogo de reglas

`govkit check` verifica un microservicio FastAPI/Cloud Run contra un estándar de servicio. Cada regla tiene un
**ID estable**, una **severidad** y un **porqué**: una regla sin motivo es una costumbre, no un estándar.

- **Crítico** (`exit 1` por defecto): compromete seguridad u operación (credenciales, observabilidad rota, servicio público).
- **Medio**: deuda que degrada la mantenibilidad o el costo; se corrige pronto.
- **Bajo**: higiene.

El análisis de código usa el **AST de Python** (no expresiones regulares sobre el texto): un comentario que diga
"severity" no cuenta como cumplimiento. Límite conocido: una cadena literal o un docstring sí cuenta como cadena del
módulo; la regla comprueba que el formatter *use* la clave, no que la ejecute.

## Estructura (S)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| S00 | crítico | Que los `.py` y `.yaml` analizados sean válidos | Un archivo roto impide verificar el resto |
| S01 | crítico | Existe el paquete (`api/`, `app/` o `src/`) con `main.py`, `core/config.py`, `core/logging.py`, `core/middleware.py`, `routers/health.py` | Es el esqueleto común: cualquier persona sabe dónde mirar |
| S02 | crítico/medio | `Dockerfile`, `requirements.txt` (crítico); `.dockerignore`, `.env.example` (medio) | Sin ellos el servicio no es construible ni configurable de forma reproducible |
| S03 | medio | `cloudbuild.yaml` | La configuración de despliegue vive en el repo, no en la consola de alguien |
| S04 | medio | Algún test ejercita `/health` | El mínimo que impide desplegar un servicio que ni arranca |

## Código de la aplicación (A)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| A01 | crítico | `setup_logging()` se llama **antes** de crear `FastAPI()` | Si no, los logs de arranque y de uvicorn salen sin formato |
| A02 | crítico | Se registra el middleware de correlación | Sin él no hay `X-Correlation-ID` ni traza en los logs |
| A03 | crítico | `health.py` define `/health` y `/version`, y el router se monta **sin prefijo** | Son endpoints de infraestructura: sin versión ni autenticación (sondas, balanceador) |
| A04 | medio | Los endpoints de negocio están versionados (`include_router(prefix="/api/v1")`, `APIRouter(prefix=...)`, rutas con `/v1` o `routers/v1/`) | Permite evolucionar el contrato sin romper a los consumidores |
| A05 | crítico/medio | `BaseSettings` con `service_name`, `service_version`, `environment`, `log_level` (crítico); `get_settings()` con `@lru_cache` (medio) | Configuración tipada y leída una vez, no en cada request |
| A06 | crítico | `logging.py` emite `severity`, `timestamp`, `logging.googleapis.com/trace`, `correlation_id`, serializa con `json.dumps` y define `setup_logging()` | Cloud Logging clasifica por `severity` (no `level`) y correlaciona por el campo de traza |
| A07 | crítico/medio | `BaseHTTPMiddleware` con `X-Correlation-ID`, `X-Cloud-Trace-Context`, `traceparent`, `request_completed` y un `ContextVar` (crítico); `uuid4` (medio) | El contexto en un `ContextVar` evita mezclar trazas entre requests concurrentes |
| A08 | medio | `main.py` no define endpoints de negocio | `main.py` solo compone |

## Contenedor (C)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| C01 | crítico | La imagen final corre como usuario **no-root** | Un compromiso del proceso no debe ser root dentro del contenedor |
| C02 | medio | Build multi-stage | La imagen final no arrastra compiladores ni cachés |
| C03 | crítico | El **comando de arranque** usa `$PORT` | Cloud Run decide el puerto. Un `ENV PORT=8080` no prueba nada; la forma exec (`["uvicorn", "--port", "$PORT"]`) **no expande** variables; `["sh", "-c", "..."]` sí. Se interpreta como lo hace Docker: si un `CMD [ ... ]` no es JSON válido, es forma shell |
| C04 | medio | Imágenes base con versión fija (no `latest`) | Builds reproducibles |
| C05 | medio | `.dockerignore` excluye `.env`, `__pycache__`, `tests`, `.git` | Un `.env` o el historial dentro de la imagen filtra secretos |
| C06 | crítico | Ningún `ENV`/`ARG` fija el valor de un secreto | Quedaría en las capas de la imagen para siempre |

## Variables de entorno y secretos (E)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| E01 | crítico | `.env.example` sin credenciales reales | Se detecta por el **nombre** (`*_PASSWORD`, `*_TOKEN`, `*_KEY`...) y por la **forma** del valor (`esquema://usuario:clave@host`, claves de Google/AWS/GitHub, claves privadas). El valor **nunca** se imprime en el reporte |
| E02 | medio | Toda variable de `config.py` está en `.env.example` | Si no está documentada, nadie sabe que existe |
| E03 | crítico | `.gitignore` excluye `.env` | Un `.env` local no debe poder llegar al repositorio |

Convención de valores aceptados en `.env.example`: vacío, `*****`, `changeme`, `<…>`, `${…}`, `your-…`, `example…`,
o cualquier valor que empiece por `demo` (p. ej. `demo-token`), y valores numéricos o booleanos (`TOKEN_TTL_SECONDS=3600`).

## Despliegue (D)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| D01 | crítico/medio | La imagen se etiqueta con `COMMIT_SHA` (crítico); las substitutions usadas tienen valor por defecto y hay `timeout` (medio) | Un tag mutable impide saber qué corre y volver atrás |
| D02 | crítico | Sin `--allow-unauthenticated` | Un servicio público lo puede invocar cualquiera en internet |
| D03 | crítico | Los secretos no van en `--set-env-vars` | Las variables de entorno son visibles para quien tenga lectura sobre el servicio; los secretos van en Secret Manager (`--set-secrets`) |
| D04 | medio | `min-instances` por defecto en 0 | Una instancia siempre encendida cuesta aunque no haya tráfico (se exceptúa con motivo) |
| D05 | medio | `--service-account` explícito | Sin él se usa la cuenta por defecto de Compute, demasiado amplia |
| D06 | crítico | Los pipelines no usan llaves de service account | Se usa federación de identidades (ver `cloudrun-wif-cicd-kit`) |

## Documentación y dependencias (M, P)

| ID | Sev. | Comprueba | Por qué |
|---|---|---|---|
| M01 | medio | `docs/MANUAL.md` o `README.md` completado (sin `TODO`/`FIXME`/`[COMPLETAR]`) | Una plantilla vacía es peor que nada: aparenta documentación |
| M02 | medio | Sección de variables de entorno | Es lo primero que necesita quien opera el servicio |
| M03 | bajo | Menciona `/health` | |
| P01 | crítico | `fastapi`, `uvicorn`, `pydantic-settings` en `requirements.txt` | Dependencias base del estándar |
| P02 | medio | Todas con versión | Builds reproducibles |
| P03 | medio | Sin `pytest`, `ruff`... en producción | La imagen de producción no las necesita |

## Excepciones (G)

| ID | Sev. | Comprueba |
|---|---|---|
| G01 | crítico | Toda excepción de `.govkit.yml` tiene un `reason` |

Una excepción **no se oculta**: aparece en el reporte con su motivo. Una excepción sin motivo no se aplica y además
es un incumplimiento crítico. Así, "este servicio no cumple la regla X porque Y" queda por escrito y revisable.

## Qué NO verifica

Es análisis **estático del repositorio**. No consulta la nube: no sabe si el servicio desplegado tiene `ingress`
restringido, `min-instances` distinto de lo que dice el archivo, o una cuenta de runtime con demasiados roles.
Eso es otro tipo de control (auditoría del proyecto) que este kit no sustituye.
