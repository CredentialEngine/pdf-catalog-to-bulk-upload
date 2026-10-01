"""PDF text extraction.

Produces a flat, cleaned list of text lines with the layout signals the parser
needs (page, position, bold, font size). Two cleanup passes handle artifacts that
are common in catalogs exported from Word or InDesign:

1. Duplicate text layers: some PDFs draw each line twice (a "fake bold" effect or
   an extra accessibility layer). Adjacent identical or near-identical lines are
   collapsed, keeping the more complete one.
2. Page-overlap repeats: some exports repeat the last few lines of a page at the
   top of the next page. The longest tail/head overlap is removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass
class Line:
    page: int          # 1-indexed page number
    y: float
    x: float
    text: str
    bold: bool
    size: float

    @property
    def key(self) -> str:
        return _norm(self.text)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
        return False
    return sm.ratio() >= threshold


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def _read_pymupdf(path: str, page_numbers: Sequence[int]) -> List[List[Line]]:
    import pymupdf  # PyMuPDF (AGPL-3.0); see README for the licensing note

    doc = pymupdf.open(path)
    pages: List[List[Line]] = []
    for pn in page_numbers:
        data = doc[pn - 1].get_text("dict", sort=True)
        rows: List[Line] = []
        for block in data.get("blocks", []):
            for ln in block.get("lines", []):
                spans = ln.get("spans", [])
                text = "".join(s["text"] for s in spans).strip()
                if not text:
                    continue
                total = sum(len(s["text"]) for s in spans) or 1
                bold_chars = sum(
                    len(s["text"]) for s in spans
                    if "bold" in s["font"].lower() or (s.get("flags", 0) & 16)
                )
                rows.append(Line(
                    page=pn, y=round(ln["bbox"][1], 1), x=round(ln["bbox"][0], 1),
                    text=text, bold=bold_chars / total > 0.6,
                    size=round(max(s["size"] for s in spans), 1),
                ))
        rows.sort(key=lambda r: (r.y, r.x))
        pages.append(rows)
    return pages


def _read_pdfplumber(path: str, page_numbers: Sequence[int]) -> List[List[Line]]:
    import pdfplumber  # MIT licensed, but much slower on large catalogs

    pages: List[List[Line]] = []
    with pdfplumber.open(path) as pdf:
        for pn in page_numbers:
            page = pdf.pages[pn - 1].dedupe_chars(tolerance=1)
            rows: List[Line] = []
            for ln in page.extract_text_lines(y_tolerance=0.5, return_chars=True):
                chars = ln["chars"]
                text = ln["text"].strip()
                if not text or not chars:
                    continue
                bold = sum("bold" in c["fontname"].lower() for c in chars) / len(chars)
                rows.append(Line(
                    page=pn, y=round(ln["top"], 1), x=round(ln["x0"], 1), text=text,
                    bold=bold > 0.6, size=round(max(c["size"] for c in chars), 1),
                ))
            rows.sort(key=lambda r: (r.y, r.x))
            pages.append(rows)
    return pages


BACKENDS = {"pymupdf": _read_pymupdf, "pdfplumber": _read_pdfplumber}


def page_count(path: str, backend: str = "pymupdf") -> int:
    if backend == "pymupdf":
        import pymupdf
        return len(pymupdf.open(path))
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def parse_page_range(spec: Optional[str], total: int) -> List[int]:
    """Parse '343-676', '1,5,10-12', '343-' or None (all pages) into 1-indexed pages."""
    if not spec:
        return list(range(1, total + 1))
    pages: List[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
            pages.extend(range(start, min(end, total) + 1))
        elif part:
            pages.append(int(part))
    return [p for p in pages if 1 <= p <= total]


def read_pages(path: str, pages: Optional[str] = None, backend: str = "pymupdf") -> List[List[Line]]:
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {sorted(BACKENDS)}")
    numbers = parse_page_range(pages, page_count(path, backend))
    return BACKENDS[backend](path, numbers)


# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------

@dataclass
class CleanStats:
    layer_duplicates: int = 0
    overlap_lines: int = 0
    dropped_by_pattern: int = 0


def collapse_layer_duplicates(lines: List[Line], threshold: float, stats: CleanStats) -> List[Line]:
    """Drop a line that repeats one of the previous two kept lines on the page.

    Exact repeats are always dropped. Near-repeats (lossy duplicate layers) are
    collapsed only for non-bold lines, keeping the longer version, so adjacent
    headings such as 'MUS 101: Topics I' / 'MUS 102: Topics II' survive.
    """
    kept: List[Line] = []
    for ln in lines:
        dup = False
        for prev_i in range(len(kept) - 1, max(len(kept) - 3, -1), -1):
            prev = kept[prev_i]
            if ln.key == prev.key:
                dup = True
                break
            if (not ln.bold and not prev.bold and len(ln.key) > 30
                    and _similar(ln.key, prev.key, threshold)):
                if len(ln.text) > len(prev.text):
                    kept[prev_i] = ln
                dup = True
                break
        if dup:
            stats.layer_duplicates += 1
        else:
            kept.append(ln)
    return kept


def remove_page_overlap(prev_page: List[Line], page: List[Line], max_lines: int,
                        threshold: float, stats: CleanStats) -> List[Line]:
    """Remove lines at the top of `page` that repeat the bottom of `prev_page`."""
    limit = min(len(prev_page), len(page), max_lines)
    for k in range(limit, 0, -1):
        tail, head = prev_page[-k:], page[:k]
        # Bold lines (headings) must match exactly: "PSYC 286: Selected Topics" and
        # "PSYC 287: Selected Topics" are near-identical but distinct courses.
        if all(a.key == b.key or (not a.bold and not b.bold and len(a.key) > 30
                                  and _similar(a.key, b.key, threshold))
               for a, b in zip(tail, head)):
            stats.overlap_lines += k
            return page[k:]
    return page


def clean(pages: Iterable[List[Line]], dedupe_layers: bool = True, remove_overlap: bool = True,
          similarity: float = 0.9, max_overlap_lines: int = 20,
          drop_patterns: Sequence[str] = ()) -> Tuple[List[Line], CleanStats]:
    """Apply pattern drops (running headers/footers), layer dedupe and overlap removal."""
    stats = CleanStats()
    drops = [re.compile(p) for p in drop_patterns]
    flat: List[Line] = []
    prev: List[Line] = []
    for page in pages:
        if drops:
            before = len(page)
            page = [ln for ln in page if not any(d.search(ln.text) for d in drops)]
            stats.dropped_by_pattern += before - len(page)
        if dedupe_layers:
            page = collapse_layer_duplicates(page, similarity, stats)
        if remove_overlap and prev:
            page = remove_page_overlap(prev, page, max_overlap_lines, similarity, stats)
        flat.extend(page)
        if page:
            prev = page
    return flat, stats
