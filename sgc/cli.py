"""Command-line interface (technical spec section 4)."""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import List, Optional

import typer

from . import __version__, config
from .discovery import discover
from .errors import FatalError
from .pipeline import STATUS_NOT_GRADED, Prepared, prepare_all
from .redact import Redactor, parse_names
from .rubric import load_rubric
from .runlog import RunFolder, get_logger

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Sim Grader Copilot")

EXIT_OK = 0
EXIT_SOME_NOT_GRADED = 1
EXIT_FATAL = 2

# Rough budget used only for the dry-run token estimate: fixed prompt text per call.
_PROMPT_OVERHEAD_CHARS = 6000


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
    model: Optional[str] = typer.Option(None, "--model", help=f"Model ID (default {config.DEFAULT_MODEL})."),
    samples: int = typer.Option(config.DEFAULT_SAMPLES, "--samples", help="Grading samples per submission (odd)."),
    max_spend: Optional[float] = typer.Option(None, "--max-spend", help="Cost ceiling in USD."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run everything up to redaction; no model calls."),
) -> None:
    """Grade every submission in SUBMISSIONS_DIR and write a report there."""
    try:
        code = _run_grade(submissions_dir, rubric, names, model, samples, max_spend, dry_run)
    except FatalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FATAL)
    raise typer.Exit(code)


def _library_versions() -> dict:
    out = {}
    for package in ("pdfplumber", "pypdf", "python-docx", "lxml", "pydantic", "reportlab", "typer"):
        try:
            out[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            out[package] = None
    return out


def _describe(item: Prepared) -> str:
    if item.status == STATUS_NOT_GRADED:
        return f"{item.sid}  {item.filename}  NOT GRADED: {item.reason}"
    flag = ""
    if item.word_count_flag == "under_min":
        flag = f"  [under {config.WORD_COUNT_MIN} words]"
    elif item.word_count_flag == "over_max":
        flag = f"  [over {config.WORD_COUNT_MAX} words]"
    extra = f"  hidden text: {len(item.hidden)}" if item.hidden else ""
    return (
        f"{item.sid}  {item.filename}  ready  {item.word_count} words{flag}  "
        f"{item.replacements} name(s) replaced{extra}"
    )


def _run_grade(
    submissions_dir: Path,
    rubric_path: Path,
    names_path: Path,
    model: Optional[str],
    samples: int,
    max_spend: Optional[float],
    dry_run: bool,
) -> int:
    if samples < 1 or samples % 2 == 0:
        raise FatalError("--samples must be a positive odd number")
    if max_spend is not None and max_spend <= 0:
        raise FatalError("--max-spend must be greater than zero")

    rubric = load_rubric(rubric_path)
    names = parse_names(names_path)
    redactor = Redactor(names)
    candidates = discover(submissions_dir)  # also checks that the directory is readable and writable

    if not dry_run:
        raise FatalError("the model stages are not implemented yet; run with --dry-run")

    started = datetime.now()
    run = RunFolder(submissions_dir, started)
    run.create()
    try:
        get_logger().info("SGC %s dry_run=%s submissions=%d", __version__, dry_run, len(candidates))
        items: List[Prepared] = prepare_all(candidates, redactor, run)
        not_graded = [i for i in items if i.status == STATUS_NOT_GRADED]
        run.write_manifest(
            {
                "sgc_version": __version__,
                "started": started.isoformat(timespec="seconds"),
                "finished": datetime.now().isoformat(timespec="seconds"),
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "libraries": _library_versions(),
                "arguments": {
                    "submissions_dir": str(submissions_dir),
                    "rubric": str(rubric_path),
                    "names_file": str(names_path),
                    "model": model or config.DEFAULT_MODEL,
                    "samples": samples,
                    "max_spend": max_spend,
                    "dry_run": dry_run,
                },
                "rubric": {
                    "title": rubric.title,
                    "total_points": rubric.total_points,
                    "sha256": rubric.sha256,
                },
                "names_count": len(names),  # the names themselves are never written
                "submissions": [
                    {"id": i.sid, "filename": i.filename, "status": i.status, "reason": i.reason}
                    for i in items
                ],
                "counts": {"submissions": len(items), "not_graded": len(not_graded)},
            }
        )
    finally:
        run.close()

    for item in items:
        typer.echo(_describe(item))
    ready = [i for i in items if i.status != STATUS_NOT_GRADED]
    chars = sum(len(i.redacted or "") for i in ready)
    rubric_chars = len(rubric.model_dump_json())
    est_tokens = (chars + len(ready) * (rubric_chars + _PROMPT_OVERHEAD_CHARS)) // 4 * (1 + samples)
    typer.echo("")
    typer.echo(
        f"{len(ready)} ready, {len(not_graded)} not graded. "
        f"Estimated input tokens across all model calls: ~{est_tokens:,}."
    )
    typer.echo(f"Redacted text and run details: {run.run_dir}")
    typer.echo("Dry run: nothing was sent to a model and no report was written.")
    return EXIT_SOME_NOT_GRADED if not_graded else EXIT_OK
