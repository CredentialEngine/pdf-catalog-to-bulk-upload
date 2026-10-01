"""Credit value parsing.

Credits are resolved from several places, in priority order:

1. the course heading          e.g. "ACCT 121: Financial Accounting (3 credits)"
2. a labeled field             e.g. "Credits: 3" or "Credit Hours: 1-4"
3. the description text        e.g. "A student can apply for 3-6 credits (s.h.)"
4. a cross-reference           credit values stated for the same course code
                               elsewhere in the catalog (program requirement lists)
5. a configured default value

Every resolved value records its source so the QA report can flag weaker ones.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

VALUE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)(?:\s*(?:-|–|—|to|or)\s*(?P<max>\d+(?:\.\d+)?))?"
)
CODE_RE = re.compile(r"\b(?P<prefix>[A-Z]{2,5}(?:/[A-Z]{2,5})*)[\s-]?(?P<number>\d{2,4}[A-Z]?)\b")


@dataclass
class Credit:
    min: Optional[float] = None
    max: Optional[float] = None
    unit: str = ""
    source: str = ""
    raw: str = ""
    notes: List[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.min is not None


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else str(v)


def format_value(v: Optional[float]) -> str:
    return _fmt(v)


def parse_value(text: str) -> Tuple[Optional[float], Optional[float]]:
    m = VALUE_RE.search(text or "")
    if not m:
        return None, None
    lo = float(m.group("min"))
    hi = float(m.group("max")) if m.group("max") else None
    if hi is not None and hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        hi = None
    return lo, hi


def resolve_unit(text: str, unit_word_map: Dict[str, str], default: str) -> str:
    for pattern, unit in unit_word_map.items():
        if re.search(pattern, text or "", re.I):
            return unit
    return default


def normalize_code(prefix: str, number: str) -> str:
    return f"{prefix.strip().upper()} {number.strip().upper()}"


def build_cross_reference(lines: Iterable[str], pattern: str) -> Dict[str, Counter]:
    """Map course code -> Counter of credit strings found outside the course section.

    Each credit statement is attached to the nearest course code that precedes it
    on the same line, so "BIOL 130 ... or BIOL 140 ... (3 credits)" credits BIOL 140.
    Slash codes ("ENGL/PRWR 333") register the value for each prefix.
    """
    credit_re = re.compile(pattern, re.I)
    table: Dict[str, Counter] = defaultdict(Counter)
    for text in lines:
        codes = [(m.start(), m) for m in CODE_RE.finditer(text)]
        if not codes:
            continue
        for cm in credit_re.finditer(text):
            preceding = [m for pos, m in codes if pos < cm.start()]
            if not preceding:
                continue
            code_m = preceding[-1]
            value = re.sub(r"\s+", "", cm.group("credits")).replace("–", "-")
            for prefix in code_m.group("prefix").split("/"):
                table[normalize_code(prefix, code_m.group("number"))][value] += 1
    return table


class CreditResolver:
    def __init__(self, cfg: dict, cross_reference: Optional[Dict[str, Counter]] = None):
        c = cfg["credits"]
        self.default_unit = c["default_unit_type"]
        self.unit_map = c["unit_word_map"]
        self.from_description = c["from_description"]
        self.desc_patterns = [re.compile(p, re.I) for p in c["description_patterns"]]
        self.exclude_ctx = [re.compile(p, re.I) for p in c.get("description_exclude_context", [])]
        self.max_plausible = float(c["max_plausible"])
        self.default_value = c.get("default_value")
        self.xref = cross_reference or {}

    def _excluded(self, text: str, start: int) -> bool:
        """True if the words just before a match describe a limit, not the course's credit
        (e.g. "Repeatable up to 15 s.h.", "will not exceed 4 semester hours")."""
        window = text[max(0, start - 60):start]
        # Cut at a real sentence/clause boundary ("week. (6 c.h." or "; "), not at the
        # period inside abbreviations like "s.h. with" or at a bare "(".
        cuts = list(re.finditer(r"[.;]\s+(?=[A-Z(])", window))
        if cuts:
            window = window[cuts[-1].end():]
        return any(rx.search(window) for rx in self.exclude_ctx)

    def _make(self, text: str, unit_text: str, source: str) -> Credit:
        lo, hi = parse_value(text)
        cr = Credit(min=lo, max=hi, source=source, raw=text.strip(),
                    unit=resolve_unit(unit_text, self.unit_map, self.default_unit))
        if lo is not None and (hi or lo) > self.max_plausible:
            cr.notes.append(f"implausible value {text.strip()!r} from {source}")
            return Credit(notes=cr.notes)
        return cr

    def resolve(self, code: str, heading_credits: str = "", heading_text: str = "",
                label_credits: str = "", description: str = "") -> Credit:
        notes: List[str] = []
        if heading_credits:
            cr = self._make(heading_credits, heading_text, "heading")
            if cr.found:
                return cr
            notes += cr.notes
        if label_credits:
            cr = self._make(label_credits, label_credits, "label")
            if cr.found:
                return cr
            notes += cr.notes
        if self.from_description and description:
            for pat in self.desc_patterns:
                for m in pat.finditer(description):
                    if self._excluded(description, m.start()):
                        continue
                    unit_text = m.groupdict().get("unit") or m.group(0)
                    cr = self._make(m.group("credits"), unit_text, "description")
                    if cr.found:
                        cr.notes = notes
                        return cr
                    notes += cr.notes
        counts = self.xref.get(code)
        if counts:
            value, n = counts.most_common(1)[0]
            cr = self._make(value, "", "cross_reference")
            if len(counts) > 1:
                detail = ", ".join(f"{v} (x{k})" for v, k in counts.most_common())
                cr.notes.append(f"conflicting cross-reference values: {detail}")
            cr.notes = notes + cr.notes
            if cr.found:
                return cr
        if self.default_value not in (None, ""):
            cr = self._make(str(self.default_value), "", "default")
            cr.notes = notes
            return cr
        return Credit(notes=notes)
