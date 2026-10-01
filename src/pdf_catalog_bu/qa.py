"""Quality assurance for extracted courses and bulk upload rows."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List

from .ctid import CTID_RE, CTID_V4_RE

QA_COLUMNS = ["Row", "Record Identifier", "Severity", "Field", "Issue", "Value Sample", "Suggested Action"]

ENCODING_ARTIFACTS = ["â€", "Â", "ï»¿", "Ã©", "Ã", "\ufffd"]
BOILERPLATE_PATTERNS = [
    r"^see (catalog|website|advisor|department)",
    r"^(n/?a|none|tbd|to be determined|coming soon|not available)\.?$",
]
URL_RE = re.compile(r"^https?://\S+$")
# Required properties of the Publisher Learning Opportunity bulk upload
REQUIRED_BU = ["External Identifier", "Learning Type", "Learning Opportunity Name", "Description",
               "Subject Webpage", "Life Cycle Status Type", "Language"]
# Enumerations from the Publisher bulk upload documentation (compared ignoring spaces/case)
ENUMS = {
    "Learning Type": {"Course", "Learning Program", "Learning Opportunity"},
    "Credit Unit Type": {"AcademicYear", "ClockHour", "ContactHour", "CarnegieUnit", "QuarterHour",
                         "SemesterHour", "CertificateCredit", "ContinuingEducationUnit", "CompetencyCredit",
                         "DegreeCredit", "DualCredit", "RequirementCredit", "SecondaryDiplomaCredit"},
    "Delivery Type": {"BlendedDelivery", "InPerson", "OnlineOnly", "Asynchronous", "MixedSynchronous",
                      "Synchronous", "VariableSite"},
    "Is Non-Credit": {"true", "false"},
}
URL_COLUMNS = ["Subject Webpage", "In Catalog", "Available Online At", "Availability Listing"]
IDENTIFIER_COLUMNS = ["Version Identifier", "Identifier"]  # TypeName~Value[~TypeURI]
RECOMMENDED_BU = ["Credit Unit Type", "Credit Unit Value"]

ACTIONS = {
    "missing_required": "Fill in before upload; the Registry will reject the row.",
    "missing_recommended": "Add credit information from the online catalog or registrar if available.",
    "short_description": "Check the PDF; the description may have been cut off or merged into another course.",
    "long_description": "Check for two courses merged together (a missed heading).",
    "number_length_anomaly": "Course number is shorter/longer than usual for this catalog; likely a typo in the source.",
    "duplicate_code": "Same course code appears more than once; keep one or confirm both are distinct.",
    "shared_description": "Different codes share identical text; usually fine (sequenced/topics courses).",
    "range_code": "Code covers a range of course numbers; decide whether to publish one record or one per number.",
    "credits_from_cross_reference": "Credit value came from a program requirement list, not the course entry; spot-check.",
    "credits_from_description": "Credit value was read from description text; confirm it describes the course.",
    "credits_conflict": "Program pages list different credit values for this course; verify.",
    "credits_variable": "Variable credit range; confirm min/max.",
    "credits_missing": "No credits found anywhere in the catalog for this course.",
    "encoding_artifact": "Fix garbled characters.",
    "all_caps_title": "Convert title to title case if desired.",
    "possible_boilerplate": "Replace placeholder text with a real description.",
    "invalid_url": "Provide a valid https URL.",
    "non_https": "Prefer https URLs.",
    "shared_subject_webpage": "Every course points to the same page; a course-specific URL is better if the catalog has one.",
    "custom_field": "Review the custom field rule or add a mapping.",
    "html_tags": "Strip HTML markup.",
    "missing_required_column": "Add this required column to the template/columns list.",
    "invalid_enumeration": "Use one of the values listed in the Publisher documentation.",
    "invalid_identifier_format": "Use IdentifierTypeName~Value, e.g. 'Catalog Version~2025-2026'.",
    "invalid_ctid": "A CTID must be 'ce-' plus a UUID: 39 characters (34 hex digits, 5 hyphens).",
    "ctid_not_v4": "Valid CTID but not a version 4 UUID (fine if it is an existing published CTID).",
    "duplicate_ctid": "Two rows share a CTID; check the crosswalk file.",
    "too_long": "Shorten the value to the Publisher limit.",
    "dropped_by_template": "A value was produced for a column the template lacks; add the column or ignore.",
}


def _norm_enum(v: str) -> str:
    return re.sub(r"[\s\-/]", "", v).lower()


def _sample(v: str, n: int = 80) -> str:
    v = (v or "").replace("\n", " ")
    return v if len(v) <= n else v[: n - 1] + "…"


class Report:
    def __init__(self):
        self.rows: List[Dict[str, str]] = []

    def add(self, row: int, ident: str, severity: str, fld: str, issue: str, value: str = "", action: str = ""):
        self.rows.append({
            "Row": str(row), "Record Identifier": ident, "Severity": severity, "Field": fld,
            "Issue": issue, "Value Sample": _sample(value), "Suggested Action": action or ACTIONS.get(issue, ""),
        })

    def counts(self) -> Counter:
        return Counter((r["Severity"], r["Issue"]) for r in self.rows)


def run_qa(courses: List[Dict[str, str]], bu_rows: List[Dict[str, str]], row_notes: List[dict]) -> Report:
    rep = Report()

    # ---- dataset-level patterns ----
    len_by_prefix: Dict[str, Counter] = defaultdict(Counter)
    for c in courses:
        digits = re.match(r"\d+", c["number"])
        if digits:
            len_by_prefix["*"][len(digits.group())] += 1
    # A number length is "anomalous" only if it is rare in this catalog (<2% of courses),
    # so legitimate schemes like KU's 2-digit gen-ed numbers (ANTH 10) are not flagged.
    total_nums = sum(len_by_prefix["*"].values()) or 1
    rare_lengths = {n for n, k in len_by_prefix["*"].items() if k / total_nums < 0.02}
    code_counts = Counter(c["code"] for c in courses)
    desc_groups: Dict[str, List[str]] = defaultdict(list)
    for c in courses:
        if len(c["description"]) > 40:
            desc_groups[c["description"].lower()].append(c["code"])
    webpages = Counter(r.get("Subject Webpage", "") for r in bu_rows)

    if bu_rows:
        for col in REQUIRED_BU:
            if col not in bu_rows[0]:
                rep.add(0, "(all rows)", "error", col, "missing_required_column")
    enum_norm = {col: {_norm_enum(v) for v in vals} for col, vals in ENUMS.items()}
    ctid_counts = Counter(r.get("CTID", "") for r in bu_rows if r.get("CTID"))

    for i, (c, bu) in enumerate(zip(courses, bu_rows), start=1):
        ident = f"{c['code']}: {c['title']}"[:90]
        for col, allowed in enum_norm.items():
            for val in filter(None, (x.strip() for x in str(bu.get(col, "")).split("|"))):
                if _norm_enum(val) not in allowed:
                    rep.add(i, ident, "error", col, "invalid_enumeration", val)
        ctid = bu.get("CTID", "")
        if ctid and not CTID_RE.match(ctid):
            rep.add(i, ident, "error", "CTID", "invalid_ctid", ctid)
        elif ctid and not CTID_V4_RE.match(ctid):
            rep.add(i, ident, "info", "CTID", "ctid_not_v4", ctid)
        if ctid and ctid_counts[ctid] > 1:
            rep.add(i, ident, "error", "CTID", "duplicate_ctid", ctid)
        for col in IDENTIFIER_COLUMNS:
            for val in filter(None, (x.strip() for x in str(bu.get(col, "")).split("|"))):
                if "~" not in val:
                    rep.add(i, ident, "error", col, "invalid_identifier_format", val)
        if len(bu.get("Coded Notation", "")) > 100:
            rep.add(i, ident, "error", "Coded Notation", "too_long", bu["Coded Notation"])
        for col in URL_COLUMNS:
            if len(bu.get(col, "")) > 600:
                rep.add(i, ident, "error", col, "too_long", bu[col])
        # required / recommended fields in the upload row
        for col in REQUIRED_BU:
            if col in bu and not str(bu[col]).strip():
                rep.add(i, ident, "error", col, "missing_required")
        # description checks
        d = c["description"]
        if d and len(d) < 40:
            rep.add(i, ident, "warning", "Description", "short_description", d)
        if len(d) > 2500:
            rep.add(i, ident, "warning", "Description", "long_description", d)
        if any(re.match(p, d.strip().lower()) for p in BOILERPLATE_PATTERNS):
            rep.add(i, ident, "warning", "Description", "possible_boilerplate", d)
        for col in ("Description", "Learning Opportunity Name"):
            val = bu.get(col, "")
            if any(a in val for a in ENCODING_ARTIFACTS):
                rep.add(i, ident, "warning", col, "encoding_artifact", val)
            if re.search(r"<[a-zA-Z][^>]*>", val):
                rep.add(i, ident, "warning", col, "html_tags", val)
        t = c["title"]
        if len(t) > 5 and t == t.upper() and re.search(r"[A-Z]{3}", t):
            rep.add(i, ident, "info", "Learning Opportunity Name", "all_caps_title", t)
        # code checks
        digits = re.match(r"\d+", c["number"])
        if digits and len(digits.group()) in rare_lengths:
            rep.add(i, ident, "warning", "Coded Notation", "number_length_anomaly", c["code"])
        if code_counts[c["code"]] > 1:
            rep.add(i, ident, "warning", "Coded Notation", "duplicate_code", c["code"])
        if "-" in c["number"]:
            rep.add(i, ident, "info", "Coded Notation", "range_code", c["code"])
        if len(desc_groups.get(d.lower(), [])) > 1:
            others = [x for x in desc_groups[d.lower()] if x != c["code"]]
            rep.add(i, ident, "info", "Description", "shared_description", "also: " + ", ".join(others[:5]))
        # credits
        src = c.get("credit_source", "")
        if not c.get("credit_min"):
            rep.add(i, ident, "warning", "Credit Unit Value", "credits_missing")
        elif src == "cross_reference":
            rep.add(i, ident, "info", "Credit Unit Value", "credits_from_cross_reference", c["credit_min"])
        elif src == "description":
            rep.add(i, ident, "warning", "Credit Unit Value", "credits_from_description", c.get("credit_raw", ""))
        if c.get("credit_max"):
            rep.add(i, ident, "info", "Credit Unit Max Value", "credits_variable", f"{c['credit_min']}-{c['credit_max']}")
        for note in (c.get("notes") or "").split(" | "):
            if note.startswith("conflicting cross-reference"):
                rep.add(i, ident, "warning", "Credit Unit Value", "credits_conflict", note)
        # URLs
        for col in URL_COLUMNS:
            url = bu.get(col, "")
            if url and not URL_RE.match(url):
                rep.add(i, ident, "error", col, "invalid_url", url)
            elif url.startswith("http://"):
                rep.add(i, ident, "info", col, "non_https", url)
        # custom field notes
        for note in row_notes[i - 1]["notes"]:
            if note.startswith("not in template"):
                rep.add(i, ident, "warning", "template", "dropped_by_template", note)
            elif not note.startswith("duplicate course code"):
                rep.add(i, ident, "info", "custom", "custom_field", note)

    if bu_rows and len(webpages) == 1 and next(iter(webpages)):
        rep.add(0, "(all rows)", "info", "Subject Webpage", "shared_subject_webpage", next(iter(webpages)))
    return rep


def summary_markdown(courses, bu_rows, rep: Report, stats: dict, warnings: List[str]) -> str:
    n = len(courses)
    with_credits = sum(1 for c in courses if c.get("credit_min"))
    src = Counter(c.get("credit_source") or "none" for c in courses)
    subjects = len({c["subject_area"] for c in courses if c.get("subject_area")})
    lines = [
        "# Extraction & QA Summary", "",
        f"- Section detected: {stats.get('section', '')} (pages {stats.get('section_pages', '')})",
        f"- Courses extracted: **{n}** across {subjects} subject areas",
        f"- Bulk upload rows written: **{len(bu_rows)}**; columns written: {stats.get('columns_written', '')}"
        + (f" ({stats['columns_removed']} empty columns removed)" if stats.get("columns_removed") else ""),
        f"- CTIDs: {stats.get('ctids_minted', 0)} new (UUID v4), {stats.get('ctids_reused', 0)} reused from crosswalk",
        f"- Courses with credits: {with_credits} ({(with_credits / n * 100 if n else 0):.1f}%)",
        "- Credit sources: " + ", ".join(f"{k}: {v}" for k, v in src.most_common()),
        f"- Duplicate text-layer lines removed: {stats.get('layer_duplicates', 0)}",
        f"- Page-overlap lines removed: {stats.get('overlap_lines', 0)}",
        f"- Boilerplate field values discarded: {stats.get('boilerplate_fields_discarded', 0)}",
        "",
    ]
    if warnings:
        lines += ["## Configuration warnings", ""] + [f"- {w}" for w in warnings] + [""]
    lines += ["## QA issues", "", "| Severity | Issue | Count | Suggested action |", "|---|---|---|---|"]
    order = {"error": 0, "warning": 1, "info": 2}
    for (sev, issue), cnt in sorted(rep.counts().items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        lines.append(f"| {sev} | {issue} | {cnt} | {ACTIONS.get(issue, '')} |")
    return "\n".join(lines) + "\n"
