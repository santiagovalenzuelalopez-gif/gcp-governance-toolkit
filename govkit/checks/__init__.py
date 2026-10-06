from pathlib import Path

from govkit.checks import appcode, cicd, container, env, hygiene, structure
from govkit.context import RepoContext, load_context
from govkit.report import Report

CHECKS = [
    structure.run,
    appcode.run,
    container.run,
    env.run,
    cicd.run,
    hygiene.run_docs,
    hygiene.run_deps,
]


def check_repo(root: str | Path, package: str | None = None) -> Report:
    ctx: RepoContext = load_context(Path(root).resolve(), package)
    for run in CHECKS:
        run(ctx)
    return ctx.report
