# GCP Governance Toolkit

[![CI](https://github.com/santiagovalenzuelalopez-gif/gcp-governance-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/santiagovalenzuelalopez-gif/gcp-governance-toolkit/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Tests](https://img.shields.io/badge/tests-102-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

**Gobernanza como código** para una plataforma de microservicios en Cloud Run. Un estándar que vive en un documento se
incumple sin que nadie se entere; aquí el estándar es **ejecutable**:

| | Qué hace | Cómo se usa |
|---|---|---|
| **`govkit check`** | Verifica un repositorio contra 35 reglas con ID, severidad y justificación: logging JSON con `severity` y traza, Correlation-ID, `/health` y `/version`, contenedor no-root con `$PORT`, secretos fuera del repo, despliegue privado... | En local, en el CI (código de salida) o como skill de Claude |
| **`govkit observability`** | Genera y aplica alertas, dashboards, métricas de logs, presupuesto y limpieza del registro, **idempotente** y con `--dry-run` | `render` → `apply --dry-run` → `apply` |

```mermaid
flowchart LR
    R[Repositorio del servicio] -->|govkit check| C{Reglas S·A·C·E·D·M·P}
    W[.govkit.yml<br/>excepciones con motivo] --> C
    C -->|texto · JSON · Markdown| O[Reporte + código de salida]
    O --> CI[CI / skill de Claude]
    P[Proyecto GCP] -->|govkit observability| G[Alertas · Dashboards<br/>Métricas · Presupuesto]
```

## Instalar y usar

```bash
pip install -e ".[dev]"        # o: pip install git+https://github.com/<usuario>/gcp-governance-toolkit

govkit check ../mi-servicio                    # reporte en texto
govkit check . --format md                     # para pegar en un PR
govkit check . --format json                   # para otras herramientas
govkit check . --fail-on medium                # en el CI: falla también con hallazgos medios
```

Ejemplo (salida real sobre uno de los servicios de este portafolio):

```
Repositorio: rag-ingest-eventarc   Paquete: app

  ✗ S03  MEDIO    Falta cloudbuild.yaml (build + deploy como código) [cloudbuild.yaml]
       → Un servicio sin cloudbuild.yaml no es reproducible: la config de despliegue vive solo en la consola
  ✗ A04  MEDIO    No hay endpoints de negocio versionados (...) [app/main.py]
       → Si el servicio solo expone un webhook interno, declarar una excepción justificada en .govkit.yml

Resumen: 0 críticos · 2 medios · 0 bajos · 0 exceptuados · 47 OK
Estado: CUMPLE CON OBSERVACIONES
```

En el CI:

```yaml
- run: pip install git+https://github.com/<usuario>/gcp-governance-toolkit
- run: govkit check . --fail-on critical
```

### Excepciones justificadas, no silenciadas

Algunos servicios incumplen una regla a propósito (un webhook interno no se versiona con `/v1`). Se declara en
[`.govkit.yml`](examples/govkit.yml) **con un motivo**; la excepción aparece en el reporte, no desaparece. Una excepción
**sin motivo** no se aplica y es un incumplimiento crítico (`G01`): no se puede "hacer pasar" el reporte sin explicarlo.

## Decisiones de diseño

- **AST, no regex.** El código se analiza con el árbol sintáctico de Python: un comentario que diga `# severity` no
  cuenta como cumplimiento, y se distinguen `@router.get("/health")` de una cadena cualquiera.
- **Docker tal como lo lee Docker.** Un `CMD [ "$X" = a ] || ...` no es JSON, así que Docker lo trata como forma shell;
  un `CMD ["uvicorn", "--port", "$PORT"]` (forma exec) **no expande** `$PORT`; un `["sh", "-c", "..."]` sí. El
  verificador replica esas reglas en lugar de buscar la palabra `PORT`.
- **El reporte nunca imprime un secreto.** Las credenciales se detectan por el *nombre* de la variable y por la
  *forma* del valor (`esquema://usuario:clave@host`, claves de Google/AWS/GitHub, claves privadas), pero solo se
  imprime el nombre: el reporte acaba en logs de CI.
- **Solo lectura.** `check` nunca modifica el repositorio (un test lo comprueba byte a byte).
- **Observabilidad con lecciones, no con plantillas.** Cada decisión (agrupar por servicio, ventanas sostenidas,
  umbral sobre el máximo real, presupuesto sobre gasto real, `--no-dry-run` explícito) está en
  [docs/OBSERVABILIDAD.md](docs/OBSERVABILIDAD.md) y **es un test**.

## Probado contra servicios reales

El verificador se ejecutó sobre los cinco microservicios FastAPI de este portafolio. Ninguno tiene hallazgos críticos;
los que quedan son reales (y no se retocaron para el reporte):

| Servicio | Críticos | Medios | Hallazgos |
|---|---|---|---|
| `multitenant-rag-chatbot` | 0 | 2 | sin `cloudbuild.yaml`; `.env.example` sin `MAX_HISTORY_CHARS` |
| `rag-ingest-eventarc` | 0 | 2 | sin `cloudbuild.yaml`; webhook sin versionar (candidato a excepción) |
| `file-service-fastapi` | 0 | 1 | sin `cloudbuild.yaml` |
| `excel-reports-service` | 0 | 3 | sin `cloudbuild.yaml`; `.env.example` sin `SPOOL_MAX_BYTES`; README sin sección de variables |
| `ai-ticket-triage-agents` | 0 | 5 | sin `cloudbuild.yaml`; sin test de `/health`; sin versionar; 4 variables sin documentar; README sin sección de variables |

Durante esta prueba el propio verificador mostró dos huecos en sus reglas (aceptaba un `ENV PORT=8080` aunque el
`CMD` tuviera el puerto fijo, y tomaba la palabra española "todo" como marcador de plantilla): ambos se corrigieron y
quedaron como tests.

## Observabilidad

```bash
govkit observability render --project my-project --channel projects/my-project/notificationChannels/123 --out out
govkit observability apply  --project my-project --channel projects/my-project/notificationChannels/123 --dry-run
```

7 políticas de alerta (5xx, latencia p95, memoria, CPU, errores de aplicación, errores de BD y saturación de VPC),
2 dashboards (vista general y detalle de un servicio con variable de plantilla), 3 métricas de logs, presupuesto
mensual y política de limpieza del registro. Ver [docs/OBSERVABILIDAD.md](docs/OBSERVABILIDAD.md).

## Skill de Claude

[`skills/service-standard-review/SKILL.md`](skills/service-standard-review/SKILL.md): revisa y corrige un repositorio en
dos fases. El **análisis lo hace `govkit check`** (determinista, el mismo resultado que verá el CI); el modelo
interpreta, propone y aplica correcciones **solo con confirmación**, y nunca inventa datos de documentación.

## Tests

```bash
pytest -q        # 102 tests, sin red ni credenciales
ruff check .
```

- **Verificador**: un repositorio de referencia 100 % conforme se **estropea de una regla a la vez** (38 casos) y se
  comprueba que se dispara la regla y con la severidad correcta; formatos de salida, excepciones, valores sensibles
  que no aparecen en ningún formato, falsos positivos conocidos (valores `demo-…`, `TOKEN_TTL_SECONDS=3600`, la palabra
  "todo") y que no modifica el repositorio.
- **Observabilidad**: invariantes de diseño (agrupación, ventanas, documentación, grilla de dashboards sin
  solapamientos, cálculo del presupuesto) y el aplicador con un ejecutor de `gcloud` falso (idempotencia, dry-run,
  `already exists`, limpieza simulada salvo confirmación).

## Estructura

```
govkit/
  cli.py                 govkit check | observability render|apply|verify-iam
  context.py, report.py  contexto del repo (AST, excepciones) y reporte (texto/JSON/Markdown)
  checks/                structure · appcode · container · env · cicd · hygiene
  observability/         alerts · dashboards · metrics · budget · render · apply
docs/                    ESTANDAR.md (catálogo de reglas) · OBSERVABILIDAD.md
skills/                  service-standard-review (skill de Claude)
examples/govkit.yml      excepciones justificadas
```

## Alcance y límites

- Es análisis **estático** del repositorio: no consulta la nube (no sabe el `ingress` real de un servicio desplegado).
- Está pensado para el estándar descrito en [docs/ESTANDAR.md](docs/ESTANDAR.md) (FastAPI + Cloud Run + Cloud Build);
  las reglas son código y se adaptan o se extienden.
- La parte de observabilidad no se ha ejecutado contra un proyecto real en esta forma genérica (los tests usan un
  ejecutor falso); los artefactos provienen de una configuración que sí se aplicó en un entorno real de preproducción.

## Licencia

MIT
