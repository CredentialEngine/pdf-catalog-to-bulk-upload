"""Command-line interface.

    pdf-catalog-bu init my-college.yaml                 # write a starter config
    pdf-catalog-bu inspect catalog.pdf --pages 343-345   # see lines, fonts, heading matches
    pdf-catalog-bu run catalog.pdf -c my-college.yaml -o output/
    pdf-catalog-bu extract catalog.pdf -c my-college.yaml -o output/courses_extracted.csv
    pdf-catalog-bu transform output/courses_extracted.csv -c my-college.yaml -o output/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import __version__, pdf_reader
from .config import load_config, validate, write_example
from .parser import INTERMEDIATE_COLUMNS
from .pipeline import extract, read_csv, run, transform_and_qa, write_csv


def _overrides(args) -> dict:
    o: dict = {}
    if getattr(args, "org_ctid", None):
        o.setdefault("organization", {})["ctid"] = args.org_ctid
    if getattr(args, "catalog_url", None):
        o.setdefault("catalog", {})["url"] = args.catalog_url
    if getattr(args, "version_identifier", None):
        o.setdefault("catalog", {})["version_identifier"] = args.version_identifier
    if getattr(args, "pages", None) and args.command != "inspect":
        o.setdefault("extraction", {})["pages"] = args.pages
    if getattr(args, "template", None):
        o.setdefault("transform", {})["template_csv"] = args.template
    if getattr(args, "backend", None):
        o.setdefault("extraction", {})["backend"] = args.backend
    return o


def _load(args):
    cfg = load_config(args.config, _overrides(args))
    warnings = validate(cfg)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return cfg, warnings


def cmd_init(args) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite", file=sys.stderr)
        return 1
    write_example(path)
    print(f"Wrote starter config to {path}")
    return 0


def cmd_inspect(args) -> int:
    cfg = load_config(args.config)
    heading = re.compile(cfg["extraction"]["heading_pattern"])
    pages = pdf_reader.read_pages(args.pdf, args.pages or "1-3", args.backend or cfg["extraction"]["backend"])
    lines, stats = pdf_reader.clean(pages)
    print(f"{'page':>4} {'size':>5} {'B':1} {'H':1}  text")
    for ln in lines[: args.limit]:
        is_h = "H" if heading.match(ln.text) else " "
        print(f"{ln.page:>4} {ln.size:>5} {'B' if ln.bold else ' '} {is_h}  {ln.text[:110]}")
    print(f"\n(B = bold, H = matches heading_pattern; removed {stats.layer_duplicates} duplicate-layer "
          f"and {stats.overlap_lines} page-overlap lines)")
    return 0


def cmd_extract(args) -> int:
    cfg, _ = _load(args)
    rows, stats = extract(args.pdf, cfg)
    out = Path(args.output)
    if out.suffix.lower() != ".csv":
        out = out / "courses_extracted.csv"
    write_csv(out, rows, INTERMEDIATE_COLUMNS, cfg["output"]["encoding"])
    print(f"Extracted {len(rows)} courses ({stats['section']}) -> {out}")
    return 0


def cmd_transform(args) -> int:
    cfg, warnings = _load(args)
    rows = read_csv(Path(args.courses_csv))
    paths = transform_and_qa(rows, cfg, Path(args.output), {"section": "from intermediate CSV"}, warnings)
    print(f"Transformed {len(rows)} courses")
    for k, p in paths.items():
        print(f"  {k:12} {p}")
    return 0


def cmd_run(args) -> int:
    cfg, warnings = _load(args)
    paths = run(args.pdf, cfg, Path(args.output), warnings)
    print((Path(paths["qa_summary"])).read_text(encoding="utf-8"))
    for k, p in paths.items():
        print(f"  {k:12} {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdf-catalog-bu", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, needs_pdf=True):
        if needs_pdf:
            sp.add_argument("pdf", help="catalog PDF")
        sp.add_argument("-c", "--config", help="YAML config/profile")
        sp.add_argument("--org-ctid", help="organization CTID (overrides config)")
        sp.add_argument("--catalog-url", help="catalog URL (overrides config)")
        sp.add_argument("--version-identifier", help='e.g. "2025-2026 Undergraduate Catalog"')
        sp.add_argument("--pages", help='page range for the course section, e.g. "343-676"')
        sp.add_argument("--backend", choices=sorted(pdf_reader.BACKENDS))
        sp.add_argument("--template", help="template CSV downloaded from the Publisher (sets output columns)")

    sp = sub.add_parser("init", help="write a starter config file")
    sp.add_argument("path")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("inspect", help="print lines with font signals to help tune patterns")
    sp.add_argument("pdf")
    sp.add_argument("-c", "--config")
    sp.add_argument("--pages", help='pages to show, default "1-3"')
    sp.add_argument("--backend", choices=sorted(pdf_reader.BACKENDS))
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("extract", help="PDF -> intermediate courses CSV")
    common(sp)
    sp.add_argument("-o", "--output", default="output/courses_extracted.csv")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("transform", help="intermediate courses CSV -> bulk upload CSV + QA")
    sp.add_argument("courses_csv")
    common(sp, needs_pdf=False)
    sp.add_argument("-o", "--output", default="output")
    sp.set_defaults(func=cmd_transform)

    sp = sub.add_parser("run", help="full pipeline: extract + transform + QA")
    common(sp)
    sp.add_argument("-o", "--output", default="output")
    sp.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
