"""Segment cleaned PDF lines into course records.

The parser walks lines in reading order and uses two signals:
  * a regex for course headings (configurable per catalog), optionally
    confirmed by bold font weight, and
  * large bold lines as subject-area headings ("Accounting", "Biology").

Everything between two course headings belongs to the first course: labeled
lines ("Prerequisites: ...") go to named fields, the rest becomes description.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .credits import CreditResolver, build_cross_reference, format_value, normalize_code
from .pdf_reader import Line

INTERMEDIATE_COLUMNS = [
    "record_id", "code", "prefix", "number", "title", "subject_area", "description",
    "prerequisites", "corequisites", "credit_min", "credit_max", "credit_unit",
    "credit_source", "credit_raw", "page_start", "page_end", "raw_heading", "notes",
]


@dataclass
class Course:
    prefix: str
    number: str
    title: str
    subject_area: str = ""
    page_start: int = 0
    page_end: int = 0
    raw_heading: str = ""
    heading_credits: str = ""
    description_lines: List[str] = field(default_factory=list)
    fields: Dict[str, List[str]] = field(default_factory=dict)
    description: str = ""
    credit_min: str = ""
    credit_max: str = ""
    credit_unit: str = ""
    credit_source: str = ""
    credit_raw: str = ""
    notes: List[str] = field(default_factory=list)
    record_id: int = 0

    @property
    def code(self) -> str:
        return normalize_code(self.prefix, self.number)

    def field_text(self, name: str) -> str:
        return join_lines(self.fields.get(name, []))

    def to_row(self) -> Dict[str, str]:
        return {
            "record_id": str(self.record_id), "code": self.code, "prefix": self.prefix,
            "number": self.number, "title": self.title, "subject_area": self.subject_area,
            "description": self.description, "prerequisites": self.field_text("prerequisites"),
            "corequisites": self.field_text("corequisites"), "credit_min": self.credit_min,
            "credit_max": self.credit_max, "credit_unit": self.credit_unit,
            "credit_source": self.credit_source, "credit_raw": self.credit_raw,
            "page_start": str(self.page_start), "page_end": str(self.page_end),
            "raw_heading": self.raw_heading, "notes": " | ".join(self.notes),
            **{f"field:{k}": join_lines(v) for k, v in self.fields.items()
               if k not in ("prerequisites", "corequisites", "credits")},
        }


def join_lines(lines: List[str]) -> str:
    """Join wrapped PDF lines into a paragraph, repairing hyphenated line breaks."""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-") and not out.endswith(" -") and ln[:1].islower():
            out = out + ln          # "inter-" + "national" -> keep hyphen, no space
        else:
            out = out + " " + ln
    out = out.replace("\u00a0", " ").replace("\u00ad", "")
    return re.sub(r"\s{2,}", " ", out).strip()


# --------------------------------------------------------------------------
# Section detection
# --------------------------------------------------------------------------

def find_section(lines: List[Line], cfg: dict) -> Tuple[int, int, str]:
    """Return (start_index, end_index_exclusive, how) for the course-description section."""
    ex = cfg["extraction"]
    if ex.get("pages"):
        return 0, len(lines), f"pages {ex['pages']} (configured)"

    heading_re = re.compile(ex["heading_pattern"])
    start_re = re.compile(ex["section_start_pattern"], re.I) if ex.get("section_start_pattern") else None
    end_re = re.compile(ex["section_end_pattern"], re.I) if ex.get("section_end_pattern") else None
    min_size = float(ex["subject_heading_min_size"])

    start = None
    if start_re:
        # Prefer a large heading match; fall back to any exact line match.
        candidates = [i for i, ln in enumerate(lines) if start_re.search(ln.text)]
        big = [i for i in candidates if lines[i].size >= min_size]
        pool = big or candidates
        # Pick the candidate followed by the most course headings in the next 40 lines
        # (skips table-of-contents entries).
        def density(i: int) -> int:
            return sum(1 for ln in lines[i:i + 40] if _is_heading(ln, heading_re, ex))
        if pool:
            start = max(pool, key=density)
            if density(start) == 0:
                start = None
    how = "section heading" if start is not None else "whole document (no section heading found)"
    if start is None:
        start = 0

    gap_limit = int(ex.get("stop_after_pages_without_course") or 0)
    end = len(lines)
    last_heading_page = lines[start].page if lines else 0
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if end_re and ln.size >= min_size and end_re.search(ln.text):
            end, how = i, how + " -> end pattern"
            break
        if _is_heading(ln, heading_re, ex):
            last_heading_page = ln.page
        elif gap_limit and ln.page - last_heading_page > gap_limit:
            # back up to the first line after the last course's page
            j = i
            while j > start and lines[j - 1].page > last_heading_page:
                j -= 1
            end, how = j, how + f" -> {gap_limit} pages without a course"
            break
    return start, end, how


def _is_heading(ln: Line, heading_re: re.Pattern, ex: dict) -> bool:
    if ex["require_bold_heading"] and not ln.bold:
        return False
    return bool(heading_re.match(ln.text))


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def parse(lines: List[Line], cfg: dict, xref_lines: Optional[List[str]] = None,
          section_bounds: Optional[Tuple[int, int, str]] = None) -> Tuple[List[Course], dict]:
    ex = cfg["extraction"]
    heading_re = re.compile(ex["heading_pattern"])
    subject_re = re.compile(ex["subject_heading_pattern"]) if ex.get("subject_heading_pattern") else None
    min_size = float(ex["subject_heading_min_size"])
    start_re = re.compile(ex["section_start_pattern"], re.I) if ex.get("section_start_pattern") else None
    labels = [(re.compile(rf"^\s*(?:{pat})\s*[:\-–]\s*(?P<rest>.*)$", re.I), name)
              for pat, name in ex["label_fields"].items()]

    start, end, how = section_bounds or find_section(lines, cfg)
    section = lines[start:end]

    courses: List[Course] = []
    current: Optional[Course] = None
    active_field: Optional[str] = None
    subject = ""
    title_open = False  # allow wrapped bold title lines right after a heading

    for ln in section:
        text = ln.text.strip()
        is_subject = (ln.bold and ln.size >= min_size) or bool(subject_re and subject_re.match(text))
        if is_subject and not heading_re.match(text):
            if not (start_re and start_re.search(text)):
                subject = text
            title_open = False
            active_field = None
            continue

        m = heading_re.match(text) if (ln.bold or not ex["require_bold_heading"]) else None
        if m:
            current = Course(
                prefix=m.group("prefix"), number=re.sub(r"\s", "", m.group("number")).replace("–", "-"),
                title=m.group("title").strip().rstrip(".").strip(), subject_area=subject,
                page_start=ln.page, page_end=ln.page, raw_heading=text,
                heading_credits=(m.groupdict().get("credits") or "").strip(),
            )
            courses.append(current)
            active_field = None
            title_open = True
            continue

        if current is None:
            continue
        current.page_end = ln.page

        if title_open and ln.bold and not is_subject:
            current.title = f"{current.title} {text}".strip()
            current.raw_heading += " " + text
            continue
        title_open = False

        label_hit = None
        for rx, name in labels:
            lm = rx.match(text)
            if lm:
                label_hit = (name, lm.group("rest"))
                break
        if label_hit:
            current.fields.setdefault(label_hit[0], []).append(label_hit[1])
            single = set(ex.get("single_line_fields") or [])
            active_field = None if label_hit[0] in single else label_hit[0]
            continue

        if active_field:
            current.fields[active_field].append(text)
        else:
            current.description_lines.append(text)

    # ---------------- post-processing ----------------
    strip_res = [re.compile(p, re.I) for p in ex.get("description_strip_patterns", [])]
    discard_res = [re.compile(p, re.I) for p in ex.get("discard_field_patterns", [])]
    stats = {"section": how, "section_lines": len(section),
             "section_pages": f"{section[0].page}-{section[-1].page}" if section else "",
             "boilerplate_fields_discarded": 0, "strip_pattern_hits": 0}

    xref = {}
    if cfg["credits"]["cross_reference"] and xref_lines:
        xref = build_cross_reference(xref_lines, cfg["credits"]["cross_reference_pattern"])
    stats["cross_reference_codes"] = len(xref)
    resolver = CreditResolver(cfg, xref)

    for i, c in enumerate(courses, start=1):
        c.record_id = i
        desc = join_lines(c.description_lines)
        for rx in strip_res:
            desc, n = rx.subn("", desc)
            stats["strip_pattern_hits"] += n
        c.description = re.sub(r"\s{2,}", " ", desc).strip()
        for name in list(c.fields):
            value = join_lines(c.fields[name])
            if any(rx.search(value) for rx in discard_res):
                stats["boilerplate_fields_discarded"] += 1
                c.fields[name] = []
        cr = resolver.resolve(c.code, heading_credits=c.heading_credits, heading_text=c.raw_heading,
                              label_credits=c.field_text("credits"), description=c.description)
        c.credit_min, c.credit_max = format_value(cr.min), format_value(cr.max)
        c.credit_unit = cr.unit if cr.found else ""
        c.credit_source, c.credit_raw = cr.source, cr.raw
        c.notes.extend(cr.notes)
    return courses, stats
