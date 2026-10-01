"""Transform intermediate course rows into Credential Registry bulk upload rows.

Input is the intermediate courses CSV (or the rows the parser produced), so a
person can review and edit extracted data before transforming it.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from typing import Dict, List, Tuple

from .ctid import CtidFactory, load_crosswalk


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _render(template: str, ctx: Dict[str, str]) -> str:
    return (template or "").format_map(_SafeDict(ctx)).strip()


def _slug(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", code).strip("-")


def _flags(spec: str) -> int:
    f = 0
    for ch in (spec or ""):
        f |= {"i": re.I, "m": re.M, "s": re.S}.get(ch, 0)
    return f


def apply_custom_fields(row: Dict[str, str], specs: List[dict], source_row: Dict[str, str]) -> List[str]:
    """Fill bulk upload columns from regex rules. Returns notes for QA."""
    notes: List[str] = []
    for spec in specs:
        col = spec["column"]
        src_name = spec.get("source", "description")
        text = source_row.get(src_name, "")
        rx = re.compile(spec["pattern"], _flags(spec.get("flags", "i")))
        matches = list(rx.finditer(text)) if spec.get("multiple") else ([rx.search(text)] if rx.search(text) else [])
        values: List[str] = []
        for m in matches:
            if "value" in spec:
                v = str(spec["value"])
            elif "value" in rx.groupindex:
                v = m.group("value")
            elif rx.groups:
                v = m.group(1)
            else:
                v = m.group(0)
            v = (v or "").strip()
            if spec.get("map"):
                mapped = next((mv for mk, mv in spec["map"].items() if re.fullmatch(mk, v, re.I)), None)
                if mapped is None:
                    notes.append(f"{col}: unmapped value {v!r}")
                    continue
                v = str(mapped)
            if v and v not in values:
                values.append(v)
        if values:
            existing = row.get(col, "")
            new = "|".join(values)
            row[col] = f"{existing}|{new}" if existing and spec.get("append") else new
            if spec.get("remove_from_source") and src_name == "description":
                row["Description"] = re.sub(r"\s{2,}", " ", rx.sub("", row.get("Description", ""))).strip()
        elif spec.get("default") not in (None, "") and not row.get(col):
            row[col] = str(spec["default"])
    return notes


def template_columns(path: str) -> List[str]:
    """Header row of a template CSV downloaded from the Publisher."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    return [h.strip() for h in header if h.strip()]


def output_columns(cfg: dict) -> Tuple[List[str], bool]:
    """Return (columns, from_template). Without a template, extra fixed/custom
    columns are appended; with one, the template is authoritative."""
    tr = cfg["transform"]
    if tr.get("template_csv"):
        cols = template_columns(tr["template_csv"])
        from_template = True
    else:
        cols = list(tr["columns"])
        for extra in list(tr["fixed_values"]) + [s["column"] for s in tr["custom_fields"]]:
            if extra not in cols:
                cols.append(extra)
        from_template = False
    if tr.get("include_ctid") and "CTID" not in cols:
        cols.insert(1 if cols and cols[0] == "UploadComments" else 0, "CTID")
    return cols, from_template


def drop_empty_columns(rows: List[Dict[str, str]], columns: List[str]) -> Tuple[List[str], List[str]]:
    """Return (columns that have at least one value, columns removed)."""
    keep = [c for c in columns if any(str(r.get(c, "")).strip() for r in rows)]
    return keep, [c for c in columns if c not in keep]


def to_bulk_upload(courses: List[Dict[str, str]], cfg: dict) -> Tuple[List[Dict[str, str]], List[dict], List[str], dict]:
    """Return (bulk upload rows, row-level notes for QA, output columns, CTID stats)."""
    tr, cat, org = cfg["transform"], cfg["catalog"], cfg["organization"]
    columns, from_template = output_columns(cfg)

    factory = CtidFactory(load_crosswalk(tr.get("ctid_crosswalk")))
    code_counts = Counter(c["code"] for c in courses)
    seen: Counter = Counter()
    rows, notes = [], []

    for c in courses:
        code = c["code"]
        seen[code] += 1
        disamb = f"#{seen[code]}" if code_counts[code] > 1 and seen[code] > 1 else ""
        ctx = {**c, "code": code, "code_slug": _slug(code), "catalog_url": cat.get("url", ""),
               "org_ctid": org["ctid"], "version": cat.get("version_identifier", "")}

        row = {col: "" for col in columns}
        row["External Identifier"] = _render(tr["external_identifier_template"], ctx) + disamb
        row["CTID"] = factory.course(row["External Identifier"])
        row["Learning Type"] = tr["learning_type"]
        row["Learning Opportunity Name"] = _render(tr["name_template"], ctx)
        row["Description"] = c.get("description", "")
        row["Subject Webpage"] = _render(tr["subject_webpage_template"], ctx)
        row["Language"] = cat.get("language", "en")
        row["Coded Notation"] = code
        row["In Catalog"] = cat.get("url", "")
        row["Version Identifier"] = (_render(tr["version_identifier_template"], ctx)
                                     if cat.get("version_identifier") else "")
        if c.get("credit_min"):
            row["Credit Unit Type"] = c.get("credit_unit") or cfg["credits"]["default_unit_type"]
            row["Credit Unit Value"] = c["credit_min"]
            row["Credit Unit Max Value"] = c.get("credit_max", "")
        if tr.get("subjects_from_subject_area") and c.get("subject_area"):
            row["Subjects"] = c["subject_area"]
        if tr.get("keywords_from_subject_area") and c.get("subject_area"):
            row["Keywords"] = c["subject_area"]
        if tr.get("include_prerequisites_as_condition") and c.get("prerequisites"):
            row["ConditionProfile: External Identifier"] = f"prereq-{_slug(code)}{disamb}"
            row["ConditionProfile: Condition Type"] = "Requires"
            row["ConditionProfile: Name"] = f"Prerequisites for {code}"
            row["ConditionProfile: Description"] = c["prerequisites"]
        for col, val in tr["fixed_values"].items():
            row[col] = _render(str(val), ctx)
        row_notes = apply_custom_fields(row, tr["custom_fields"], c)
        if disamb:
            row_notes.append(f"duplicate course code {code}; External Identifier suffixed with '{disamb}'")
        dropped = [k for k, v in row.items() if v and k not in columns and k != "CTID"]
        if dropped and from_template:
            row_notes.append("not in template, value dropped: " + ", ".join(dropped))
        rows.append(row)
        notes.append({"record_id": c.get("record_id", ""), "code": code, "notes": row_notes})
    stats = {"ctids_reused": factory.reused, "ctids_minted": factory.minted}
    return rows, notes, columns, stats
