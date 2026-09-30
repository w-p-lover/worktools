#!/usr/bin/env python3
"""
Recursive batch PDF -> Excel converter.

Use case:
- Traverse one root folder recursively.
- Find PDFs in all subfolders.
- Write each output XLSX next to its source PDF (same path/same basename).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import pdf_to_excel_precise as core


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively convert PDFs to XLSX (output beside source PDF)."
    )
    parser.add_argument("root_dir", type=Path, help="Root directory to scan recursively")
    parser.add_argument(
        "--pattern",
        default="*.pdf",
        help="Glob pattern for input files (default: *.pdf)",
    )
    parser.add_argument(
        "--suffix",
        default="",
        help="Output suffix before .xlsx, e.g. _target (default: empty)",
    )
    parser.add_argument(
        "--pages",
        default="all",
        help="Pages to parse, e.g. all | 1 | 1,3-5",
    )
    parser.add_argument(
        "--engine",
        choices=["pdfplumber", "camelot", "all"],
        default="pdfplumber",
        help="Table extraction engine",
    )
    parser.add_argument(
        "--flavor",
        choices=["lattice", "stream", "both"],
        default="both",
        help="Camelot flavor when engine uses camelot/all",
    )
    parser.add_argument(
        "--target-policy",
        choices=["focused", "broad"],
        default="focused",
        help="Target extraction policy",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=4.0,
        help="Minimum candidate score",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable deduplication",
    )
    parser.add_argument(
        "--fix-superscript",
        action="store_true",
        help="Recover common superscript units",
    )
    parser.add_argument(
        "--newline-as-space",
        action="store_true",
        help="Replace intra-cell newlines with spaces",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview input/output mapping without conversion",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only first N PDFs (0 means all)",
    )
    return parser.parse_args()


def build_output_path(pdf_path: Path, suffix: str) -> Path:
    return pdf_path.with_name(f"{pdf_path.stem}{suffix}.xlsx")


def list_pdfs(root: Path, pattern: str) -> List[Path]:
    return sorted([p for p in root.rglob(pattern) if p.is_file()])


def convert_one(pdf_path: Path, out_path: Path, args: argparse.Namespace) -> Tuple[bool, str]:
    try:
        target_df = core.extract_target_records(
            input_pdf=pdf_path,
            pages=args.pages,
            flavor=args.flavor,
            engine=args.engine,
            min_score=args.min_score,
            target_policy=args.target_policy,
            dedup=not args.no_dedup,
        )
    except RuntimeError as exc:
        # Keep behavior aligned with main script: fallback to engine=all from pdfplumber.
        if args.engine == "pdfplumber":
            try:
                target_df = core.extract_target_records(
                    input_pdf=pdf_path,
                    pages=args.pages,
                    flavor=args.flavor,
                    engine="all",
                    min_score=args.min_score,
                    target_policy=args.target_policy,
                    dedup=not args.no_dedup,
                )
            except RuntimeError:
                target_df = core.empty_target_dataframe()
        else:
            return False, f"Failed: {exc}"

    core.export_target_records_to_excel(target_df, out_path)
    return True, f"Saved {len(target_df)} row(s)"


def main() -> None:
    args = parse_args()
    root = args.root_dir
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"root_dir not found or not a directory: {root}")

    core.ENABLE_SUPERSCRIPT_FIX = args.fix_superscript
    core.FLATTEN_CELL_NEWLINES = args.newline_as_space

    pdfs = list_pdfs(root, args.pattern)
    if args.limit > 0:
        pdfs = pdfs[: args.limit]

    if not pdfs:
        print("No PDF files found.")
        return

    print(f"Found {len(pdfs)} PDF file(s) under: {root}")

    if args.dry_run:
        for i, pdf_path in enumerate(pdfs, start=1):
            out_path = build_output_path(pdf_path, args.suffix)
            print(f"[DRY-RUN {i}] {pdf_path} -> {out_path}")
        print("Dry-run complete. No files were converted.")
        return

    ok_count = 0
    skip_count = 0
    fail_count = 0

    for i, pdf_path in enumerate(pdfs, start=1):
        out_path = build_output_path(pdf_path, args.suffix)
        if out_path.exists() and not args.overwrite:
            skip_count += 1
            print(f"[{i}/{len(pdfs)}] SKIP (exists): {out_path}")
            continue

        ok, msg = convert_one(pdf_path, out_path, args)
        if ok:
            ok_count += 1
            print(f"[{i}/{len(pdfs)}] OK: {pdf_path.name} -> {out_path.name} | {msg}")
        else:
            fail_count += 1
            print(f"[{i}/{len(pdfs)}] FAIL: {pdf_path.name} | {msg}")

    print(
        f"Done. total={len(pdfs)}, ok={ok_count}, skipped={skip_count}, failed={fail_count}"
    )
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

