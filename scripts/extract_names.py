#!/usr/bin/env python3
"""Scan a folder of submissions for student names, without any language model.

Usage (from the repository root, with the virtual environment active or via .venv/bin/python):

    .venv/bin/python scripts/extract_names.py ./submissions
    .venv/bin/python scripts/extract_names.py ./submissions -o names_candidates.txt

Everything runs locally. The output is a list of candidates to review, not a finished names file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sgc.namescan import LOW, format_candidates_file, scan_directory


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("submissions_dir", type=Path, help="Folder containing PDF and DOCX submissions.")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("names_candidates.txt"),
        help="Where to write the candidate names (default: names_candidates.txt).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it exists.")
    parser.add_argument("--no-file", action="store_true", help="Only print; do not write a file.")
    args = parser.parse_args(argv)

    if not args.submissions_dir.is_dir():
        print(f"error: {args.submissions_dir} is not a directory", file=sys.stderr)
        return 2
    if not args.no_file and args.output.exists() and not args.force:
        print(f"error: {args.output} already exists; use --force to overwrite it", file=sys.stderr)
        return 2

    result = scan_directory(args.submissions_dir)
    if not result.per_file:
        print("No PDF or DOCX files found.")
        return 0

    print("Per file (high/medium confidence first):")
    for filename, items in result.per_file.items():
        if filename in result.unreadable:
            print(f"  {filename}: could not be read ({result.unreadable[filename]})")
        shown = [f"{i.name} [{i.confidence}: {i.source}]" for i in items if i.confidence != LOW]
        low = [i.name for i in items if i.confidence == LOW]
        line = "; ".join(shown) if shown else "(no name found)"
        if low:
            line += f"  | low-confidence: {', '.join(low)}"
        print(f"  {filename}: {line}")

    if result.shared:
        print(f"\nExcluded as shared (in {result.shared_threshold}+ submissions; likely patients, instructor, or course):")
        for cand in result.shared:
            print(f"  {cand.name} ({cand.docs_with_text} documents)")

    if result.no_name:
        print(f"\n{len(result.no_name)} file(s) with no confident name; add these by hand:")
        for filename in result.no_name:
            print(f"  {filename}")

    confident = [c for c in result.candidates if c.confidence != LOW]
    print(f"\n{len(confident)} confident candidate(s), {len(result.candidates) - len(confident)} low-confidence.")
    if not args.no_file:
        args.output.write_text(format_candidates_file(result), encoding="utf-8")
        print(f"Wrote {args.output}. Review it, then copy the names you want into your names file.")
        print("It contains real names: do not commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
