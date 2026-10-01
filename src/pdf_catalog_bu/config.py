"""Configuration: defaults, YAML loading, and validation.

A configuration ("profile") holds everything institution-specific: the
organization CTID, catalog metadata, the regex patterns that recognize course
headings in this catalog, credit rules, fixed values for the bulk upload, and
any custom extraction targets. Users only override what differs from DEFAULTS.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List

import yaml

CTID_RE = re.compile(r"^ce-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PLACEHOLDER_CTID = "ce-00000000-0000-0000-0000-000000000000"

# Default course heading pattern. Recognizes the common forms:
#   ACCT 121: Financial Accounting
#   ACCT 121 - Financial Accounting (3 credits)
#   ACCT-121. Financial Accounting. 3 Credits.
#   BIOL 371-379: Selected Topics in Biology
#   ENGL/PRWR 333: Digital Rhetoric
# Named groups: prefix, number, title, and optionally credits.
DEFAULT_HEADING = (
    r"^(?P<prefix>[A-Z]{2,5}(?:/[A-Z]{2,5})?)[\s-]?"
    r"(?P<number>\d{1,4}[A-Z]{0,2}(?:\s?[-–]\s?\d{1,4}[A-Z]?)?)"
    r"\s*[:.\-–—]?\s+"
    r"(?P<title>[A-Z0-9\"'‘“(].*?)"
    r"(?:[\s.,]*\(?\s*(?P<credits>\d+(?:\.\d+)?(?:\s*(?:-|–|to|or)\s*\d+(?:\.\d+)?)?)"
    r"\s*(?i:credits?|credit hours?|cr\.?|s\.?h\.?|semester hours?|units?|hours?)\s*\)?\.?)?\s*$"
)

# Column order of the Publisher "Learning Opportunity" bulk upload template
# (required properties first). Override with transform.template_csv to use the
# exact header row of a template downloaded from the Publisher.
DEFAULT_COURSE_COLUMNS: List[str] = [
    "UploadComments",
    "External Identifier",
    "Learning Type",
    "Learning Opportunity Name",
    "Description",
    "Subject Webpage",
    "Life Cycle Status Type",
    "Language",
    "Teaches Competency Framework",
    "Delivery Type",
    "Credit Unit Type",
    "Credit Unit Value",
    "Credit Unit Max Value",
    "Credit Unit Type Description",
    "Prerequisites",
    "Offered By",
    "Coded Notation",
    "Keywords",
    "Subjects",
    "In Catalog",
    "Version Identifier",
    "ConditionProfile: External Identifier",
    "ConditionProfile: Condition Type",
    "ConditionProfile: Name",
    "ConditionProfile: Description",
]

DEFAULTS: Dict[str, Any] = {
    "organization": {
        "name": "",
        "ctid": PLACEHOLDER_CTID,
    },
    "catalog": {
        "url": "",                    # -> In Catalog
        "version_identifier": "",     # -> Version Identifier, e.g. "2025-2026 Undergraduate Catalog"
        "language": "English",          # Publisher sample uses language names ("english")
    },
    "extraction": {
        "backend": "pymupdf",           # pymupdf (fast) | pdfplumber (MIT, slower)
        "pages": None,                  # e.g. "343-676"; None = auto-detect section
        "section_start_pattern": r"^Course Descriptions?$",
        "section_end_pattern": None,    # regex for a large heading that ends the section
        "stop_after_pages_without_course": 3,
        "heading_pattern": DEFAULT_HEADING,
        "require_bold_heading": True,   # set False for PDFs without font weight info
        "subject_heading_min_size": 14, # bold lines at/above this size = subject area headings
        "subject_heading_pattern": None,  # optional regex alternative to font size
        "drop_line_patterns": [],       # running headers/footers, page numbers
        "dedupe_layers": True,
        "remove_page_overlap": True,
        "similarity_threshold": 0.9,
        # Lines beginning "Label: ..." become separate fields instead of description text.
        # Map label regex -> field name. A field named "credits" feeds the credit parser.
        "label_fields": {
            r"Pre-?requisites?": "prerequisites",
            r"Co-?requisites?": "corequisites",
            r"Credits?|Credit Hours|Semester Hours": "credits",
        },
        # Label fields that never continue onto following lines.
        "single_line_fields": ["credits"],
        # Field values matching these patterns are treated as empty (catalog boilerplate).
        "discard_field_patterns": [],
        # Regexes removed from descriptions (boilerplate sentences, footnote markers...).
        "description_strip_patterns": [],
    },
    "credits": {
        "default_unit_type": "SemesterHour",   # SemesterHour | QuarterHour | ContactHour | ContinuingEducationUnit ...
        "unit_word_map": {
            r"s\.?\s?h\.?|semester hours?|semester credits?": "SemesterHour",
            r"quarter hours?|quarter credits?|q\.?h\.?": "QuarterHour",
            r"contact hours?": "ContactHour",
            r"clock hours?": "ClockHour",
            r"ceus?|continuing education units?": "ContinuingEducationUnit",
        },
        # Look for credit statements inside the description text.
        "from_description": True,
        "description_patterns": [
            r"(?P<credits>\d+(?:\.\d+)?(?:\s*(?:-|–|to)\s*\d+(?:\.\d+)?)?)\s*"
            r"(?P<unit>credits?\s*\(s\.h\.\)|credit hours?|semester hours?|s\.h\.)",
        ],
        # Skip description matches preceded (same clause) by limit/repeat wording.
        "description_exclude_context": [
            r"repeat", r"\bup to\b", r"exceed", r"total of", r"maximum", r"\bmax\b", r"minimum",
            r"at least", r"permission for", r"\blimit", r"may be (taken|earned)", r"no more than",
            r"completion of", r"completed", r"\bearned\b", r"\bafter\b", r"beyond",
        ],
        "max_plausible": 18,
        # Scan pages OUTSIDE the course section (e.g. program requirement lists
        # like "ACCT 121: Financial Accounting (3 credits)") for credit values.
        "cross_reference": True,
        "cross_reference_pattern":
            r"\(\s*(?P<credits>\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?)\s*"
            r"(?P<unit>credits?|s\.h\.|credit hours?|semester hours?)\s*\)",
        "default_value": None,          # fill when nothing is found (e.g. 3) - use with care
    },
    "transform": {
        "learning_type": "Course",
        # CTIDs are new random UUID v4 values ("ce-" + uuid4). To keep CTIDs from an
        # earlier run or already-published records, point this at a CSV with CTID and
        # External Identifier columns (e.g. the previous course_bulk_upload.csv).
        "ctid_crosswalk": None,
        # Placeholders: {code} {code_slug} {prefix} {number} {catalog_url} {org_ctid}
        "subject_webpage_template": "{catalog_url}",
        "external_identifier_template": "{code}",
        # Publisher format is IdentifierTypeName~Value; blank version -> column left empty.
        "version_identifier_template": "Catalog Version~{version}",
        # Path to a template CSV downloaded from the Publisher; its header row sets the
        # output columns and order. Values for columns not in it are reported in QA.
        "template_csv": None,
        # Include a CTID column (the Publisher accepts and uses it).
        "include_ctid": True,
        # Remove columns with no value in any row from the bulk upload sheet.
        "drop_empty_columns": True,
        "name_template": "{title}",
        "subjects_from_subject_area": True,   # subject heading -> Subjects
        "keywords_from_subject_area": False,
        "include_prerequisites_as_condition": False,
        "fixed_values": {
            "Life Cycle Status Type": "Active",
            "Offered By": "same as owner",
        },
        "columns": DEFAULT_COURSE_COLUMNS,
        # Extra extraction targets that fill bulk upload columns. See README.
        "custom_fields": [],
    },
    "output": {
        "encoding": "utf-8-sig",
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("label_fields", "fixed_values", "unit_word_map"):
            out[k] = deep_merge(out[k], v)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            # Mapping-type settings: user entries are merged over defaults;
            # set a key to null to remove a default entry.
            merged = dict(out[k])
            merged.update(v)
            out[k] = {kk: vv for kk, vv in merged.items() if vv is not None}
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | None = None, overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, encoding="utf-8") as fh:
            cfg = deep_merge(cfg, yaml.safe_load(fh) or {})
    if overrides:
        cfg = deep_merge(cfg, overrides)
    return cfg


def validate(cfg: Dict[str, Any]) -> List[str]:
    """Return warnings (non-fatal). Raises ValueError for fatal problems."""
    warnings: List[str] = []
    ctid = str(cfg["organization"].get("ctid") or "")
    if not CTID_RE.match(ctid):
        raise ValueError(f"organization.ctid {ctid!r} is not a valid CTID (ce-<uuid>)")
    if ctid == PLACEHOLDER_CTID:
        warnings.append("organization.ctid is the placeholder value; set your organization's CTID.")
    for key in ("heading_pattern", "section_start_pattern", "section_end_pattern", "subject_heading_pattern"):
        pat = cfg["extraction"].get(key)
        if pat:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"extraction.{key} is not a valid regex: {exc}") from exc
    heading = re.compile(cfg["extraction"]["heading_pattern"])
    missing = {"prefix", "number", "title"} - set(heading.groupindex)
    if missing:
        raise ValueError(f"extraction.heading_pattern must define named groups: {sorted(missing)}")
    if not cfg["catalog"].get("url"):
        warnings.append("catalog.url is empty; 'In Catalog' and the default Subject Webpage will be blank.")
    if cfg["transform"].get("ctid_mode"):
        warnings.append("transform.ctid_mode is no longer used; CTIDs are always new UUID v4 "
                        "values unless reused from transform.ctid_crosswalk.")
    return warnings


def write_example(path: str | Path) -> None:
    src = Path(__file__).with_name("config_template.yaml")
    Path(path).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
