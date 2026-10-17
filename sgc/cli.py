"""Command-line interface (technical spec section 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import DEFAULT_MODEL, DEFAULT_SAMPLES
from .errors import FatalError
from .rubric import load_rubric

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Sim Grader Copilot")

EXIT_OK = 0
EXIT_SOME_NOT_GRADED = 1
EXIT_FATAL = 2


@app.callback()
def main() -> None:
    """Sim Grader Copilot: grade recommendation reports for simulation reflections."""


@app.command()
def version() -> None:
    """Print the SGC version."""
    typer.echo(__version__)


@app.command()
def grade(
    submissions_dir: Path = typer.Argument(..., help="Directory containing the submissions."),
    rubric: Path = typer.Option(..., "--rubric", help="Path to the rubric Markdown file."),
    names: Path = typer.Option(..., "--names", help="Path to the names file (one name per line)."),
    model: Optional[str] = typer.Option(None, "--model", help=f"Model ID (default {DEFAULT_MODEL})."),
    samples: int = typer.Option(DEFAULT_SAMPLES, "--samples", help="Grading samples per submission (odd)."),
    max_spend: Optional[float] = typer.Option(None, "--max-spend", help="Cost ceiling in USD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run everything up to redaction; no model calls."),
) -> None:
    """Grade every submission in SUBMISSIONS_DIR and write a report there."""
    try:
        _run_grade(submissions_dir, rubric, names, model, samples, max_spend, dry_run)
    except FatalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FATAL)


def _run_grade(
    submissions_dir: Path,
    rubric_path: Path,
    names_path: Path,
    model: Optional[str],
    samples: int,
    max_spend: Optional[float],
    dry_run: bool,
) -> None:
    if samples < 1 or samples % 2 == 0:
        raise FatalError("--samples must be a positive odd number")
    if max_spend is not None and max_spend <= 0:
        raise FatalError("--max-spend must be greater than zero")
    load_rubric(rubric_path)
    raise FatalError("grading is not implemented yet")
