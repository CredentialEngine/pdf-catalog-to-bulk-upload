"""CTID generation.

Each CTID is a standard UUID version 4 (random) prefixed with "ce-": 39 characters,
34 hexadecimal digits and 5 hyphens, e.g. ce-8c4f2a1e-3b6d-4f0a-9e2c-1d5b7a9c3e10.
See https://credreg.net/ctdl/terms/ctid.

New CTIDs are random, so re-running the pipeline would mint new ones. To keep the
CTIDs of records that are already published (or were assigned in an earlier run),
supply a crosswalk: any CSV with a CTID column plus an "External Identifier"
column, such as a previous course_bulk_upload.csv or a Publisher export. A simple
two-column file with "code,ctid" also works.
"""
from __future__ import annotations

import csv
import re
import uuid
from typing import Dict, Optional

CTID_RE = re.compile(r"^ce-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CTID_V4_RE = re.compile(r"^ce-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def new_ctid() -> str:
    """A new CTID: 'ce-' + a random (version 4) UUID, lowercase."""
    return f"ce-{uuid.uuid4()}"


def is_valid_ctid(value: str) -> bool:
    return bool(CTID_RE.match(value or ""))


def _norm_key(value: str) -> str:
    return " ".join((value or "").upper().split())


def load_crosswalk(path: Optional[str]) -> Dict[str, str]:
    """Map External Identifier (or course code) -> CTID from a CSV file."""
    if not path:
        return {}
    out: Dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
        key_col = fields.get("external identifier") or fields.get("code") or fields.get("coded notation")
        ctid_col = fields.get("ctid")
        if not key_col or not ctid_col:
            raise ValueError(f"crosswalk {path} needs a CTID column and an "
                             "'External Identifier' (or 'code') column")
        for row in reader:
            key, ctid = _norm_key(row.get(key_col, "")), (row.get(ctid_col) or "").strip().lower()
            if key and is_valid_ctid(ctid):
                out[key] = ctid
    return out


class CtidFactory:
    """Reuse crosswalk CTIDs where available; otherwise mint new UUID v4 CTIDs."""

    def __init__(self, crosswalk: Optional[Dict[str, str]] = None):
        self.crosswalk = crosswalk or {}
        self.reused = 0
        self.minted = 0

    def course(self, external_identifier: str) -> str:
        ctid = self.crosswalk.get(_norm_key(external_identifier))
        if ctid:
            self.reused += 1
            return ctid
        self.minted += 1
        return new_ctid()
