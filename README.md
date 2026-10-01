# pdf-catalog-to-bulk-upload

Turn a **PDF course catalog** into **Credential Publisher bulk upload sheets**.

Many institutions publish their course catalog only as a PDF. This tool reads the
PDF, finds each course entry, extracts the **course code, name, description and
credits**, and writes a Learning Opportunity bulk upload CSV (one row per course)
ready for review and publishing to the [Credential Registry](https://credentialengine.org/)
through the Credential Engine Publisher. It is for PDF sources only; catalogs
published as web pages need a website extractor instead.

| | |
|---|---|
| Project / repo | `pdf-catalog-to-bulk-upload` |
| Command | `pdf-catalog-bu` |
| Python package | `pdf_catalog_bu` | Everything institution-specific lives in a small YAML
profile, so the same tool works across catalogs.

```
catalog.pdf ──► extract ──► courses_extracted.csv ──► transform ──► course_bulk_upload.csv
                              (review / edit here)                    qa_report.csv
                                                                      qa_summary.md
```

The intermediate CSV is a deliberate checkpoint: you can correct extraction issues in
a spreadsheet and re-run only the transform step.

## Quick start

```bash
pip install git+https://github.com/CredentialEngine/pdf-catalog-to-bulk-upload.git   # or: pip install -e .

pdf-catalog-bu init my-college.yaml          # 1. starter profile; set your org CTID, catalog URL
pdf-catalog-bu inspect catalog.pdf --pages 200-202   # 2. look at the course pages
pdf-catalog-bu run catalog.pdf -c my-college.yaml -o output/   # 3. full pipeline
```

`run` prints a QA summary and writes four files to `output/`:

| File | Contents |
|---|---|
| `courses_extracted.csv` | One row per course: code, title, subject area, description, prerequisites, credits with their source, PDF page numbers |
| `course_bulk_upload.csv` | Learning Opportunity / Course bulk upload sheet (UTF-8 with BOM) |
| `qa_report.csv` | One row per issue: severity, field, value sample, suggested action |
| `qa_summary.md` | Counts of courses, credit coverage, cleanup statistics, issues by type |

To re-transform after editing the intermediate file:

```bash
pdf-catalog-bu transform output/courses_extracted.csv -c my-college.yaml -o output/
```

Common settings can also be passed on the command line:
`--org-ctid`, `--catalog-url`, `--version-identifier`, `--pages`, `--backend`.

## How it works

**1. Reading the PDF.** Text is read line by line along with font size and weight,
which are strong signals in most catalogs: course headings are usually bold and
subject areas ("Accounting", "Biology") are larger bold headings.

**2. Cleaning.** Catalogs exported from Word or InDesign often contain artifacts that
corrupt naive text extraction. Two are handled automatically:

- *Duplicate text layers.* Some PDFs draw every line twice, sometimes slightly offset
  or with dropped characters. Adjacent duplicates are collapsed, keeping the more
  complete copy.
- *Repeated page tops.* Some exports repeat the last lines of a page at the top of
  the next page. The overlap is detected and removed. Headings must match exactly,
  so "PSYC 286: Selected Topics" and "PSYC 287: Selected Topics" are never merged.

**3. Finding the course section.** The parser looks for the section heading (default
`Course Descriptions`), choosing the occurrence followed by course entries rather
than a table-of-contents line. The section ends at an optional end pattern, or after
several pages without a course. You can also set `pages` explicitly.

**4. Segmenting courses.** Each line matching the heading pattern starts a new course.
Following lines are assigned to that course: `Label: text` lines (Prerequisites,
Corequisites, Credits…) go to named fields, and the rest becomes the description.
Wrapped lines are rejoined into paragraphs.

**5. Resolving credits**, in priority order, recording which source was used:

1. the heading, e.g. `ACCT 121 - Financial Accounting (3 credits)`
2. a labeled line, e.g. `Credits: 3`
3. the description, e.g. `(6 c.h., 3 s.h.)` or `apply for 3-6 credits (s.h.)`; phrases
   describing limits ("repeatable up to 15 s.h.", "not to exceed 4 semester hours")
   are skipped
4. a **cross-reference**: values stated for the same course code elsewhere in the
   catalog, typically program requirement lists. When values conflict, the most
   common one is used and the conflict is flagged.
5. an optional configured default

Variable credit (`1-6`) fills both *Credit Unit Value* and *Credit Unit Max Value*.

**6. Transforming.** Each course becomes a bulk upload row with fixed values,
templated URLs and identifiers, and any custom fields you define.

**7. QA.** Checks include missing required fields, very short or long descriptions
(cut-off or merged entries), duplicate course codes, unusual course-number lengths
(often typos in the source), range codes such as `BIOL 371-379`, credit provenance and
conflicts, encoding artifacts, HTML remnants and invalid URLs.

## Matching the Publisher template

The Publisher generates the bulk upload template from the properties you select, so
the column set varies. Download your template (with or without the sample and
instruction rows) and pass it in:

```bash
pdf-catalog-bu run catalog.pdf -c my-college.yaml --template LearningOpportunity_Bulk_Upload.csv -o output/
```

The output then uses the template's columns in the same order, plus `CTID`, minus
any column left empty. If the
tool produced a value for a column your template lacks (for example credits, when
the credit columns weren't selected), the QA report lists it as `dropped_by_template`.
Without `--template`, a built-in column list based on the Learning Opportunity
template is used.

Values follow the Publisher's formats: `Learning Type` is `Course`, credit units use
the Publisher's credit unit values (`SemesterHour`, `QuarterHour`, …), `Version
Identifier` is written as `IdentifierTypeName~Value` (default `Catalog
Version~{version}`), `Offered By` defaults to `same as owner`, and the subject area
heading goes to `Subjects`. QA checks required columns, enumerations, identifier
formats and length limits.

## CTIDs

Each course gets a **CTID**: a standard random (version 4) UUID prefixed with `ce-`,
39 characters in all (34 hexadecimal digits and 5 hyphens), as defined at
[credreg.net](https://credreg.net/ctdl/terms/ctid). The bulk upload sheet includes a
`CTID` column, which the Publisher uses when provided. QA verifies every CTID's
format and uniqueness.

Because CTIDs are random, running the tool again would assign new ones. To keep the
CTIDs of courses that were already published, or assigned in an earlier run, point
`transform.ctid_crosswalk` at a CSV with `CTID` and `External Identifier` columns,
such as the previous `course_bulk_upload.csv` or a Publisher export:

```yaml
transform:
  ctid_crosswalk: output/2025-26/course_bulk_upload.csv
```

Matching courses keep their CTIDs; new courses get new ones. The QA summary reports
how many were reused and how many were minted. Set `transform.include_ctid: false` to
leave CTID assignment to the Publisher.

## Empty columns

Columns with no value in any row are removed from the bulk upload sheet, so it
contains only the properties you are actually publishing. Set
`transform.drop_empty_columns: false` to keep the full template layout.

## Configuration reference

A profile only needs the settings that differ from the defaults. See
`src/pdf_catalog_bu/config_template.yaml` (written by `pdf-catalog-bu init`) and the
example in `profiles/`.

| Setting | Purpose |
|---|---|
| `organization.ctid` | **Required.** Your organization's CTID |
| `catalog.url`, `catalog.version_identifier`, `catalog.language` | *In Catalog*, *Version Identifier*, *Language* |
| `extraction.pages` | Page range of the course section, e.g. `"343-676"` (otherwise auto-detected) |
| `extraction.section_start_pattern` / `section_end_pattern` | Regexes for the section's first heading and the heading that follows it |
| `extraction.heading_pattern` | Course heading regex with named groups `prefix`, `number`, `title` and optionally `credits` |
| `extraction.require_bold_heading` | Require headings to be bold (set `false` if the PDF lacks font weights) |
| `extraction.subject_heading_min_size` | Bold lines at least this size are subject areas |
| `extraction.drop_line_patterns` | Regexes for running headers, footers and page numbers to discard |
| `extraction.label_fields` | `Label: text` regex → field name; `credits` feeds the credit resolver |
| `extraction.discard_field_patterns` | Field values to treat as boilerplate |
| `extraction.description_strip_patterns` | Regexes removed from descriptions |
| `credits.default_unit_type` | `SemesterHour`, `QuarterHour`, `ContactHour`, `ContinuingEducationUnit`, … |
| `credits.from_description`, `description_patterns`, `description_exclude_context` | Credit detection in description text |
| `credits.cross_reference` | Use credits stated elsewhere in the catalog |
| `credits.default_value` | Fallback value when nothing is found |
| `transform.template_csv` | Publisher template CSV whose header row sets the output columns (same as `--template`) |
| `transform.include_ctid` | Include the CTID column (default `true`) |
| `transform.ctid_crosswalk` | CSV of existing CTIDs keyed by External Identifier, to reuse instead of minting new ones |
| `transform.drop_empty_columns` | Remove columns with no values (default `true`) |
| `transform.version_identifier_template` | Default `Catalog Version~{version}` (Publisher format `TypeName~Value`) |
| `transform.subjects_from_subject_area` / `keywords_from_subject_area` | Where subject area headings go (Subjects by default) |
| `transform.subject_webpage_template` | URL template; placeholders `{code}` `{code_slug}` `{prefix}` `{number}` `{title}` `{catalog_url}` |
| `transform.fixed_values` | Constant values for any column, e.g. `Life Cycle Status Type: Active` |
| `transform.columns` | Output header list and order when no template is given |
| `transform.include_prerequisites_as_condition` | Write prerequisites to *ConditionProfile* columns |
| `transform.custom_fields` | Additional extraction targets (below) |

### Custom extraction targets

Fill any bulk upload column from extracted text with a regex rule:

```yaml
transform:
  custom_fields:
    - column: Delivery Type
      source: description            # any column of courses_extracted.csv
      pattern: '\b(fully )?online\b'
      value: OnlineOnly               # constant when the pattern matches
    - column: Keywords
      source: description
      pattern: '\b(writing intensive|service learning)\b'
      multiple: true                  # all matches, pipe-delimited
      append: true                    # add to an existing value
    - column: Is Non-Credit
      source: title
      pattern: '(?P<value>non-?credit)'
      map: {'non-?credit': 'true'}    # map captured text to a vocabulary value
```

Each rule takes `column` and `pattern`, plus optional `source`, `value`, `map`,
`multiple`, `append`, `default`, `flags` (default `i`) and `remove_from_source`. A new
column is appended to the output if it isn't already in `transform.columns`.

## Adapting to a new catalog

1. Run `pdf-catalog-bu inspect catalog.pdf --pages <a few course pages>`. Lines marked
   `H` match the heading pattern and `B` are bold.
2. If headings aren't marked `H`, adjust `extraction.heading_pattern`. Common formats
   (`ACCT 121: Title`, `ACCT 121 - Title (3 credits)`, `ACCT-121. Title. 3 Credits.`)
   already match the default.
3. Run `extract` and open `courses_extracted.csv`. Sort by description length to find
   merged or truncated entries.
4. Run `run` and work through `qa_summary.md`, errors first.

## Example: Kutztown University 2025-2026

`profiles/kutztown-2025-2026.yaml` processes a 704-page catalog. It extracts all
2,293 course headings across 79 subject areas and removes about 9,700 duplicate-layer
lines and 350 repeated page-top lines. The catalog's course entries state no
credits, so about half are filled from program requirement lists and description
notes. See `examples/kutztown/qa_summary.md`.

## PDF backends and licensing

The default backend, [PyMuPDF](https://pymupdf.readthedocs.io/), is fast (about
5 seconds for a 700-page catalog) and handles duplicate text layers well. It is
licensed under **AGPL-3.0** (commercial licenses are available from Artifex). This
project is MIT licensed, but anyone redistributing it together with PyMuPDF must also
meet PyMuPDF's license terms.

An alternative backend uses [pdfplumber](https://github.com/jsvine/pdfplumber)
(MIT): `pip install pdf-catalog-to-bulk-upload[pdfplumber]`, then `--backend pdfplumber`. It is
much slower on large catalogs, so `pages` is recommended with it.

## Limitations

- Text-based PDFs only. Scanned catalogs need OCR first (e.g. `ocrmypdf`).
- Multi-column layouts are read in position order; check `inspect` output for
  interleaved columns.
- Credits found by cross-reference or in descriptions are heuristics and are flagged
  for review.
- Competency/learning-outcome extraction and the *Teaches Competency Framework*
  link are not yet implemented.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests use synthetic lines, so no PDF is needed.
