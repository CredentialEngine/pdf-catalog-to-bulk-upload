"""End-to-end pipeline: PDF -> intermediate courses CSV -> bulk upload CSV + QA."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

from . import pdf_reader
from .parser import INTERMEDIATE_COLUMNS, find_section, parse
from .qa import QA_COLUMNS, run_qa, summary_markdown
from .transform import drop_empty_columns, to_bulk_upload


def write_csv(path: Path, rows: List[Dict[str, str]], columns: List[str], encoding: str = "utf-8-sig",
              strict: bool = False) -> None:
    """Write rows. strict=True writes exactly `columns` (bulk upload files)."""
    extra = [] if strict else [k for r in rows for k in r if k not in columns]
    cols = columns + list(dict.fromkeys(extra))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=encoding, newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def read_csv(path: Path) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def extract(pdf_path: str, cfg: dict) -> Tuple[List[Dict[str, str]], dict]:
    ex = cfg["extraction"]
    clean_kwargs = dict(dedupe_layers=ex["dedupe_layers"], remove_overlap=ex["remove_page_overlap"],
                        similarity=ex["similarity_threshold"], drop_patterns=ex["drop_line_patterns"])
    need_xref = cfg["credits"]["cross_reference"]

    if ex.get("pages"):
        section_pages = pdf_reader.read_pages(pdf_path, ex["pages"], ex["backend"])
        lines, cstats = pdf_reader.clean(section_pages, **clean_kwargs)
        bounds = (0, len(lines), f"configured pages {ex['pages']}")
        xref_lines: List[str] = []
        if need_xref:
            wanted = {ln.page for page in section_pages for ln in page}
            all_pages = pdf_reader.read_pages(pdf_path, None, ex["backend"])
            others, _ = pdf_reader.clean([p for p in all_pages if p and p[0].page not in wanted], **clean_kwargs)
            xref_lines = [ln.text for ln in others]
    else:
        all_pages = pdf_reader.read_pages(pdf_path, None, ex["backend"])
        lines, cstats = pdf_reader.clean(all_pages, **clean_kwargs)
        bounds = find_section(lines, cfg)
        start, end, _ = bounds
        xref_lines = [ln.text for ln in lines[:start] + lines[end:]] if need_xref else []

    courses, stats = parse(lines, cfg, xref_lines=xref_lines, section_bounds=bounds)
    stats.update(layer_duplicates=cstats.layer_duplicates, overlap_lines=cstats.overlap_lines,
                 dropped_by_pattern=cstats.dropped_by_pattern)
    return [c.to_row() for c in courses], stats


def transform_and_qa(course_rows: List[Dict[str, str]], cfg: dict, out_dir: Path, stats: dict,
                     warnings: List[str], prefix: str = "") -> Dict[str, Path]:
    enc = cfg["output"]["encoding"]
    bu_rows, notes, columns, ctid_stats = to_bulk_upload(course_rows, cfg)
    report = run_qa(course_rows, bu_rows, notes)
    stats = {**stats, **ctid_stats, "columns_total": len(columns)}
    if cfg["transform"].get("drop_empty_columns", True):
        columns, removed = drop_empty_columns(bu_rows, columns)
        stats["columns_removed"] = len(removed)
    stats["columns_written"] = len(columns)
    paths = {
        "bulk_upload": out_dir / f"{prefix}course_bulk_upload.csv",
        "qa_report": out_dir / f"{prefix}qa_report.csv",
        "qa_summary": out_dir / f"{prefix}qa_summary.md",
    }
    write_csv(paths["bulk_upload"], bu_rows, columns, enc, strict=True)
    write_csv(paths["qa_report"], report.rows, QA_COLUMNS, enc)
    paths["qa_summary"].write_text(summary_markdown(course_rows, bu_rows, report, stats, warnings), encoding="utf-8")
    return paths


def run(pdf_path: str, cfg: dict, out_dir: Path, warnings: List[str], prefix: str = "") -> Dict[str, Path]:
    course_rows, stats = extract(pdf_path, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    courses_path = out_dir / f"{prefix}courses_extracted.csv"
    write_csv(courses_path, course_rows, INTERMEDIATE_COLUMNS, cfg["output"]["encoding"])
    paths = transform_and_qa(course_rows, cfg, out_dir, stats, warnings, prefix)
    return {"courses": courses_path, **paths}
