"""Tests use synthetic Line objects, so no PDF is required."""
import re

import pytest

from pdf_catalog_bu.config import DEFAULT_HEADING, load_config, validate
from pdf_catalog_bu.credits import CreditResolver, build_cross_reference, parse_value
from pdf_catalog_bu.ctid import CTID_V4_RE, load_crosswalk, new_ctid
from pdf_catalog_bu.parser import parse
from pdf_catalog_bu.pdf_reader import Line, clean
from pdf_catalog_bu.qa import run_qa
from pdf_catalog_bu.transform import drop_empty_columns, to_bulk_upload

ORG = "ce-11111111-2222-3333-4444-555555555555"


def L(text, page=1, bold=False, size=10.0, y=0.0):
    return Line(page=page, y=y, x=50, text=text, bold=bold, size=size)


def cfg(**extraction):
    c = load_config(overrides={"organization": {"ctid": ORG},
                               "catalog": {"url": "https://catalog.example.edu/", "version_identifier": "2025-26"}})
    c["extraction"].update(extraction)
    return c


# ---------------------------------------------------------------- heading regex
@pytest.mark.parametrize("text,code,title,credits", [
    ("ACCT 121: Financial Accounting", ("ACCT", "121"), "Financial Accounting", None),
    ("ACCT 121 - Financial Accounting (3 credits)", ("ACCT", "121"), "Financial Accounting", "3"),
    ("BIOL 371-379: Selected Topics in Biology", ("BIOL", "371-379"), "Selected Topics in Biology", None),
    ("ENGL/PRWR 333: Digital Rhetoric", ("ENGL/PRWR", "333"), "Digital Rhetoric", None),
    ("MATH 105. College Algebra. 3-4 Credits.", ("MATH", "105"), "College Algebra", "3-4"),
    ("ARTH 344: 'La Dolce Vita': Italian Art", ("ARTH", "344"), "'La Dolce Vita': Italian Art", None),
])
def test_default_heading(text, code, title, credits):
    m = re.match(DEFAULT_HEADING, text)
    assert m, text
    assert (m.group("prefix"), m.group("number").replace(" ", "")) == code
    assert m.group("title").rstrip(". ") == title
    assert m.group("credits") == credits


def test_heading_rejects_prose():
    assert not re.match(DEFAULT_HEADING, "This course builds on ACCT 305, Cost Accounting.")


# ---------------------------------------------------------------- cleanup
def test_layer_duplicates_and_page_overlap():
    p1 = [L("ACCT 101: Intro", bold=True), L("First line of a long description here."),
          L("First line of a long description here."), L("Second line that continues the text.")]
    p2 = [L("Second line that continues the text.", page=2), L("ACCT 102: Next", page=2, bold=True)]
    lines, stats = clean([p1, p2])
    assert [ln.text for ln in lines] == ["ACCT 101: Intro", "First line of a long description here.",
                                         "Second line that continues the text.", "ACCT 102: Next"]
    assert stats.layer_duplicates == 1 and stats.overlap_lines == 1


def test_overlap_keeps_similar_but_distinct_headings():
    desc = "Topics of current interest will be selected by the instructor."
    p1 = [L("PSYC 286: Selected Topics in Psychology", bold=True), L(desc)]
    p2 = [L("PSYC 287: Selected Topics in Psychology", page=2, bold=True), L(desc, page=2)]
    lines, _ = clean([p1, p2])
    assert sum(ln.bold for ln in lines) == 2


def test_lossy_duplicate_layer_keeps_longer():
    p = [L("computer) and dismantling objects. Students will use the microcontroller"),
         L("compuer) and dismantlingobjects. Students willuse he microcontroller")]
    lines, _ = clean([p])
    assert len(lines) == 1 and "computer)" in lines[0].text


# ---------------------------------------------------------------- parsing
def sample_lines():
    return [
        L("Contents", size=18, bold=True),
        L("Course Descriptions", size=18, bold=True, page=2),
        L("Accounting", size=18, bold=True, page=2),
        L("ACCT 121: Financial Accounting", bold=True, page=2),
        L("The language of business. Areas of emphasis include the", page=2),
        L("accounting cycle and internal control.", page=2),
        L("Prerequisites: The current prerequisites can be found online.", page=2),
        L("ACCT 395: Honors Thesis in Accounting", bold=True, page=2),
        L("A student can apply for 3-6 credits (s.h.). Repeatable up to 12 s.h.", page=2),
        L("Biology", size=18, bold=True, page=3),
        L("BIOL 101: General Biology", bold=True, page=3),
        L("Credits: 4", page=3),
        L("An introduction to living systems.", page=3),
    ]


def test_parse_courses_fields_and_credits():
    c = cfg(discard_field_patterns=["can be found online"])
    courses, stats = parse(sample_lines(), c)
    assert [x.code for x in courses] == ["ACCT 121", "ACCT 395", "BIOL 101"]
    a, h, b = courses
    assert a.subject_area == "Accounting" and b.subject_area == "Biology"
    assert a.description == "The language of business. Areas of emphasis include the accounting cycle and internal control."
    assert a.field_text("prerequisites") == ""          # boilerplate discarded
    assert (h.credit_min, h.credit_max, h.credit_source) == ("3", "6", "description")
    assert (b.credit_min, b.credit_source) == ("4", "label")
    assert b.description == "An introduction to living systems."


def test_cross_reference_credits_attach_to_nearest_code():
    xref = build_cross_reference(
        ["BIOL 130 Environmental Issues or BIOL 140 Biology & Society (3 credits)",
         "ACCT 121: Financial Accounting (3 credits)", "ACCT 121 Financial Accounting (4 credits)",
         "ACCT 121: Financial Accounting (3 credits)"],
        load_config()["credits"]["cross_reference_pattern"])
    assert "BIOL 130" not in xref and xref["BIOL 140"]["3"] == 1
    r = CreditResolver(cfg(), xref).resolve("ACCT 121")
    assert r.min == 3 and r.source == "cross_reference" and "conflicting" in r.notes[0]


def test_description_limits_are_not_credits():
    r = CreditResolver(cfg())
    assert not r.resolve("X 1", description="The course may be taken up to two times (6 credit hours).").found
    assert not r.resolve("X 1", description="(Repeatable up to 15 s.h. with a 6 s.h. limit.)").found
    ok = r.resolve("X 1", description="A minimum of 3 hours of work is required per week. (6 c.h., 3 s.h.)")
    assert ok.min == 3


def test_parse_value():
    assert parse_value("3") == (3, None)
    assert parse_value("1 to 6") == (1, 6)
    assert parse_value("4-4") == (4, None)


# ---------------------------------------------------------------- transform + QA
def test_transform_values_and_custom_fields():
    c = cfg()
    c["transform"]["subject_webpage_template"] = "https://catalog.example.edu/search/?P={code_slug}"
    c["transform"]["custom_fields"] = [
        {"column": "Delivery Type", "source": "description", "pattern": r"\bonline\b", "value": "OnlineOnly"}]
    courses, _ = parse(sample_lines(), c)
    rows = [x.to_row() for x in courses]
    bu1, _, cols, _ = to_bulk_upload(rows, c)
    assert bu1[0]["Subject Webpage"] == "https://catalog.example.edu/search/?P=ACCT-121"
    assert bu1[1]["Credit Unit Type"] == "SemesterHour" and bu1[1]["Credit Unit Max Value"] == "6"
    assert bu1[0]["Delivery Type"] == "" and bu1[0]["Subjects"] == "Accounting"
    assert cols[:3] == ["UploadComments", "CTID", "External Identifier"]
    assert bu1[0]["Offered By"] == "same as owner"
    assert bu1[0]["Version Identifier"] == "Catalog Version~2025-26"
    rows[0]["description"] += " Offered online."
    bu3, _, _, _ = to_bulk_upload(rows, c)
    assert bu3[0]["Delivery Type"] == "OnlineOnly"


def test_duplicate_codes_get_distinct_ctids_and_qa_flags():
    c = cfg()
    courses, _ = parse(sample_lines(), c)
    rows = [x.to_row() for x in courses]
    rows.append(dict(rows[0]))
    bu, notes, _, _ = to_bulk_upload(rows, c)
    assert len({r["CTID"] for r in bu}) == len(bu)
    issues = {r["Issue"] for r in run_qa(rows, bu, notes).rows}
    assert "duplicate_code" in issues and "credits_missing" in issues


def test_validate_rejects_bad_ctid():
    with pytest.raises(ValueError):
        validate(load_config(overrides={"organization": {"ctid": "not-a-ctid"}}))


def test_template_csv_sets_columns_and_flags_dropped(tmp_path):
    tpl = tmp_path / "template.csv"
    tpl.write_text("UploadComments,External Identifier,Learning Type,Learning Opportunity Name,Description,"
                   "Subject Webpage,Life Cycle Status Type,Language,Coded Notation\n", encoding="utf-8-sig")
    c = cfg()
    c["transform"]["template_csv"] = str(tpl)
    courses, _ = parse(sample_lines(), c)
    rows = [x.to_row() for x in courses]
    bu, notes, cols, _ = to_bulk_upload(rows, c)
    assert cols[-1] == "Coded Notation" and len(cols) == 10 and cols[1] == "CTID"
    issues = {r["Issue"] for r in run_qa(rows, bu, notes).rows}
    assert "dropped_by_template" in issues          # Offered By / Subjects / credits not in template


def test_qa_enumeration_and_identifier_format():
    c = cfg()
    courses, _ = parse(sample_lines(), c)
    rows = [x.to_row() for x in courses]
    bu, notes, _, _ = to_bulk_upload(rows, c)
    bu[0]["Credit Unit Type"] = "Credits"
    bu[0]["Version Identifier"] = "2025-2026"
    issues = {r["Issue"] for r in run_qa(rows, bu, notes).rows}
    assert {"invalid_enumeration", "invalid_identifier_format"} <= issues


def test_ctids_are_random_uuid_v4():
    ids = {new_ctid() for _ in range(200)}
    assert len(ids) == 200
    for ctid in ids:
        assert len(ctid) == 39 and ctid.count("-") == 5 and CTID_V4_RE.match(ctid)
        assert sum(ch in "0123456789abcdef" for ch in ctid) == 34   # 'c','e' + 32 hex digits


def test_crosswalk_reuses_ctids_from_previous_output(tmp_path):
    c = cfg()
    courses, _ = parse(sample_lines(), c)
    rows = [x.to_row() for x in courses]
    first, _, cols, stats1 = to_bulk_upload(rows, c)
    assert stats1 == {"ctids_reused": 0, "ctids_minted": 3}
    prev = tmp_path / "previous.csv"
    import csv
    with open(prev, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(first[:2])
    c["transform"]["ctid_crosswalk"] = str(prev)
    second, _, _, stats2 = to_bulk_upload(rows, c)
    assert [r["CTID"] for r in second[:2]] == [r["CTID"] for r in first[:2]]
    assert second[2]["CTID"] != first[2]["CTID"]
    assert stats2 == {"ctids_reused": 2, "ctids_minted": 1}
    assert load_crosswalk(str(prev))["ACCT 121"] == first[0]["CTID"]


def test_empty_columns_removed():
    c = cfg()
    courses, _ = parse(sample_lines(), c)
    bu, _, cols, _ = to_bulk_upload([x.to_row() for x in courses], c)
    keep, removed = drop_empty_columns(bu, cols)
    assert "UploadComments" in removed and "Teaches Competency Framework" in removed
    assert {"CTID", "External Identifier", "Description", "Credit Unit Value"} <= set(keep)
