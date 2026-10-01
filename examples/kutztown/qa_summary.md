# Extraction & QA Summary

- Section detected: section heading -> end pattern (pages 343-676)
- Courses extracted: **2293** across 79 subject areas
- Bulk upload rows written: **2293**; columns written: 16 (109 empty columns removed)
- CTIDs: 2293 new (UUID v4), 0 reused from crosswalk
- Courses with credits: 1162 (50.7%)
- Credit sources: none: 1131, cross_reference: 1087, description: 75
- Duplicate text-layer lines removed: 9706
- Page-overlap lines removed: 353
- Boilerplate field values discarded: 911

## QA issues

| Severity | Issue | Count | Suggested action |
|---|---|---|---|
| error | missing_required | 9 | Fill in before upload; the Registry will reject the row. |
| warning | credits_missing | 1131 | No credits found anywhere in the catalog for this course. |
| warning | credits_from_description | 75 | Credit value was read from description text; confirm it describes the course. |
| warning | short_description | 46 | Check the PDF; the description may have been cut off or merged into another course. |
| warning | credits_conflict | 22 | Program pages list different credit values for this course; verify. |
| warning | number_length_anomaly | 7 | Course number is shorter/longer than usual for this catalog; likely a typo in the source. |
| warning | duplicate_code | 4 | Same course code appears more than once; keep one or confirm both are distinct. |
| info | credits_from_cross_reference | 1087 | Credit value came from a program requirement list, not the course entry; spot-check. |
| info | shared_description | 497 | Different codes share identical text; usually fine (sequenced/topics courses). |
| info | credits_variable | 36 | Variable credit range; confirm min/max. |
| info | range_code | 7 | Code covers a range of course numbers; decide whether to publish one record or one per number. |
| info | all_caps_title | 4 | Convert title to title case if desired. |
| info | shared_subject_webpage | 1 | Every course points to the same page; a course-specific URL is better if the catalog has one. |
