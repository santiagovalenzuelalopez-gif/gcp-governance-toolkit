# Observabilidad como código

Alertas, dashboards, métricas de logs, presupuesto y limpieza del registro, **generados** por funciones puras en
[`govkit/observability/`](../govkit/observability) y aplicados con `gcloud` de forma idempotente. Los JSON son
artefactos reproducibles (`govkit observability render`), no archivos pegados desde la consola.

```bash
# 1. Generar los JSON (no toca ninguna nube)
govkit observability render --project my-project --channel projects/my-project/notificationChannels/123 --out out

# 2. Simular: lee lo existente y muestra qué crearía
govkit observability apply  --project my-project --channel projects/my-project/notificationChannels/123 --dry-run

# 3. Aplicar (crea solo lo que falta). Con --billing-account y --budget-baseline crea también el presupuesto
govkit observability apply  --project my-project --channel projects/my-project/notificationChannels/123 \
    --billing-account ABCDEF-123456-ABCDEF --budget-baseline 560

# Comprobar (sin asignar) que la cuenta de runtime puede escribir logs, métricas y trazas
govkit observability verify-iam --project my-project --service-account run-sa@my-project.iam.gserviceaccount.com
```

## Lecciones de diseño (cada una es un test)

### 1. Una política por métrica, agrupada por recurso

Con decenas de servicios en un proyecto, hay dos formas malas de alertar:

- **Una política por servicio**: decenas de políticas que mantener.
- **Una política sin agrupar**: `crossSeriesReducer` **colapsa todos los servicios en una sola serie**. El incidente no
  dice cuál falló, y un servicio saturado se **diluye** al promediarse con los sanos.

Se usa una política por métrica con `groupByFields: ["resource.label.service_name"]` (o `connector_name` para VPC):
el incidente nombra el recurso afectado. `test_every_alert_is_grouped_...` lo hace cumplir.

### 2. Una alerta sin ventana es ruido

Una política de "errores de aplicación" con `duration: 0s` y umbral `> 0` abría y cerraba un incidente **por cada
log ERROR aislado**: el equipo aprendió a ignorar las alertas. Todas llevan una ventana (`300s`; `600s` en CPU y
memoria, donde los picos breves son normales) y el texto aclara que "un error aislado NO dispara esta alerta".
`test_application_errors_alert_requires_sustained_...` y `test_every_alert_is_actionable_...`.

### 3. Toda alerta lleva documentación de diagnóstico

Cada política incluye en `documentation` qué significa, qué mirar primero y un enlace al dashboard. Una alerta que
despierta a alguien y no dice qué hacer es una alerta incompleta.

### 4. Umbrales sobre el máximo real, no sobre un porcentaje inventado

La saturación de un conector VPC se mide contra su `maxThroughput` configurado: 1000 Mbps = 125.000.000 bytes/s;
al 80 % = **100.000.000 bytes/s**. Si el conector es de 100 Mbps, el umbral cambia (`vpc_connector_max_mbps`).
Y la política va agrupada por conector: promediar entre conectores **diluiría la saturación de uno solo**.

### 5. Vista general = agregados; el detalle, con variable de plantilla

- **Platform Overview** usa series *agregadas*: agrupar por servicio con decenas de servicios superpone decenas de
  líneas (lo contrario de una vista general). El desglose por servicio vive en el otro dashboard.
- **Service Detail** es **un solo dashboard** para cualquier servicio, con una variable de plantilla `service_name`
  que filtra todos los widgets.
- Los widgets se rotulan por lo que miden: "Tasa de 5xx (req/s)" con umbral "5 req/s", no "Error rate 5 %" (el widget
  nunca calculó un porcentaje: un rótulo engañoso hace tomar malas decisiones).
- Series que comparten métrica y filtro (p50/p95/p99) llevan `legendTemplate`; si no, son indistinguibles.
- Los widgets no se solapan y caben en 12 columnas (`test_dashboard_grid_...`).

### 6. Presupuesto: referencia de gasto REAL, no de un subtotal con descuentos

`budget_amount(baseline, buffer)` = gasto mensual de referencia × margen (1,5 por defecto), redondeado hacia arriba.
La referencia debe ser el **costo de uso real**, no un subtotal con descuentos: un crédito promocional temporal que
cubre casi todo el uso hace parecer baratísimo un entorno que no lo es, y el presupuesto quedaría muy por debajo del
gasto cuando el descuento termine. Tampoco un promedio histórico si la arquitectura cambió: se usa el último mes
completo con el estado **actual**, y se **revisa a la baja** cuando se retiran los servicios que hoy coexisten. Sin una
referencia > 0 el comando se niega a calcularla.

Umbrales: 50 % y 90 % del gasto real y **100 % del pronosticado** (avisa antes de pasarse, no después), a un grupo de
correo (no a una persona) a través del canal de notificación.

### 7. Limpieza del registro: `--dry-run` es "pegajoso"

`gcloud artifacts repositories set-cleanup-policies` mantiene el modo simulación hasta que se pasa
`--no-dry-run`: **omitir `--dry-run` no aplica la política**. Por eso `apply` simula por defecto y solo aplica de
verdad con `--ar-confirm` (que pasa `--no-dry-run` de forma explícita). La política conserva las N versiones más
recientes de cada imagen (`keep-last-5` por defecto).

### 8. Idempotencia y dry-run útil

`apply` consulta antes de crear (políticas y dashboards por `displayName`, métricas por `describe`) y tolera
"already exists". `--dry-run` **sí lee** lo existente (solo lectura) para mostrar un plan real, pero no ejecuta
ningún comando que modifique. El ejecutor de `gcloud` es inyectable: los tests lo sustituyen y comprueban qué se
habría ejecutado.

### 9. Roles de la cuenta de runtime: se verifican, no se asignan

`verify-iam` informa qué roles faltan (`logging.logWriter`, `monitoring.metricWriter`, `cloudtrace.agent`) pero **no
los asigna**: dar roles a nivel de proyecto es una decisión de mínimo privilegio que se toma de forma explícita.

## Métricas de logs

| Métrica | Filtro | Para qué |
|---|---|---|
| `application_errors` | `severity` = `ERROR`/`CRITICAL` | Alerta de errores sostenidos |
| `unhandled_exceptions` | `jsonPayload.exception != ""` | El campo `exception` lo agrega el `JsonFormatter` del estándar (regla A06) |
| `db_connection_errors` | `jsonPayload.message =~ "<patrón>"` | Patrón configurable según los drivers del proyecto |

Dependen de que los servicios emitan **logs JSON con `severity`**: por eso el verificador de estándar (A06) y esta
parte se complementan.

## Qué no hace

- No habilita `servicecontrol.googleapis.com` (métricas nativas del gateway): tiene impacto en costo y posibles
  efectos sobre un gateway ya desplegado; es una decisión que requiere análisis aparte.
- No crea el canal de notificación (hay que pasar el existente). Los comandos de canales y políticas están en el
  track `beta` de `gcloud`.
- No se ha ejecutado contra un proyecto real en esta forma genérica: los tests usan un ejecutor falso. Los
  artefactos provienen de una configuración que sí se aplicó en un entorno real de preproducción.
