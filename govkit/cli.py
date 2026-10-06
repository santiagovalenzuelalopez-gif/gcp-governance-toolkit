"""Línea de comandos: ``govkit check`` y ``govkit observability``."""

import argparse
import sys
from pathlib import Path

from govkit.checks import check_repo
from govkit.report import Severity


def _check(args: argparse.Namespace) -> int:
    report = check_repo(Path(args.repo), args.package)
    sys.stdout.write(report.render(args.format) + "\n")
    return report.exit_code(Severity(args.fail_on))


def _render(args: argparse.Namespace) -> int:
    from govkit.observability.render import render_all

    written = render_all(args.project, args.channel, Path(args.out), dashboard_url=args.dashboard_url,
                         budget_baseline=args.budget_baseline, budget_buffer=args.budget_buffer)
    for path in written:
        sys.stdout.write(f"  + {path}\n")
    return 0


def _apply(args: argparse.Namespace) -> int:
    from govkit.observability.apply import SubprocessRunner, apply_all

    results = apply_all(
        SubprocessRunner(), project=args.project, channel=args.channel, out=Path(args.out),
        billing_account=args.billing_account, budget_baseline=args.budget_baseline, budget_buffer=args.budget_buffer,
        dry_run=args.dry_run, ar_repository=args.ar_repository, ar_location=args.ar_location, ar_confirm=args.ar_confirm,
    )
    for line in results:
        sys.stdout.write(line + "\n")
    return 0


def _verify_iam(args: argparse.Namespace) -> int:
    from govkit.observability.apply import SubprocessRunner, verify_runtime_iam

    missing = verify_runtime_iam(SubprocessRunner(), args.project, args.service_account)
    for role in missing:
        sys.stdout.write(f"  ✗ FALTA {role}\n")
    if not missing:
        sys.stdout.write("  ✓ La cuenta de runtime tiene los roles de observabilidad\n")
    return int(bool(missing))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="govkit", description="Gobernanza como código para microservicios en Cloud Run")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Verifica un repositorio contra el estándar de servicio")
    check.add_argument("repo", nargs="?", default=".", help="raíz del repositorio (por defecto, el directorio actual)")
    check.add_argument("--package", help="paquete de la aplicación (se detecta: api/, app/ o src/)")
    check.add_argument("--format", choices=["text", "json", "md"], default="text")
    check.add_argument("--fail-on", choices=[s.value for s in Severity], default=Severity.CRITICAL.value,
                       help="severidad mínima que hace fallar (código de salida 1)")
    check.set_defaults(handler=_check)

    obs = sub.add_parser("observability", help="Alertas, dashboards, métricas y presupuesto como código")
    obs_sub = obs.add_subparsers(dest="obs_command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", required=True)
        p.add_argument("--channel", required=True, help="notificationChannels/<id> (canal de correo de un grupo)")
        p.add_argument("--out", default="out", help="directorio de salida de los JSON")
        p.add_argument("--budget-baseline", type=float, default=0.0, help="gasto mensual real de referencia (USD)")
        p.add_argument("--budget-buffer", type=float, default=1.5, help="multiplicador de seguridad sobre la referencia")

    render = obs_sub.add_parser("render", help="Genera los JSON (no toca ninguna nube)")
    common(render)
    render.add_argument("--dashboard-url", default="", help="enlace al dashboard para la documentación de las alertas")
    render.set_defaults(handler=_render)

    apply = obs_sub.add_parser("apply", help="Aplica con gcloud (idempotente; --dry-run para simular)")
    common(apply)
    apply.add_argument("--billing-account", default="", help="si se indica, crea también el presupuesto")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--ar-repository", default="", help="repo de Artifact Registry al que aplicar la política de limpieza")
    apply.add_argument("--ar-location", default="us-central1")
    apply.add_argument("--ar-confirm", action="store_true", help="aplicar la limpieza de verdad (por defecto solo simula)")
    apply.set_defaults(handler=_apply)

    iam = obs_sub.add_parser("verify-iam", help="Comprueba (sin asignar) los roles de observabilidad de la cuenta de runtime")
    iam.add_argument("--project", required=True)
    iam.add_argument("--service-account", required=True)
    iam.set_defaults(handler=_verify_iam)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
