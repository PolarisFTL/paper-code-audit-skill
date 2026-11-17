# Audit JSON contract · v1.0

The agent performs the semantic review. Helpers validate provenance, source ranges and arithmetic; they cannot decide scientific equivalence automatically. Python 3.10+ is sufficient for scoring/rendering.

## Input preparation

```text
python SKILL/scripts/prepare_inputs.py --paper PAPER.pdf --code SOURCE_OR_ARCHIVE --out NEW_JOB
```

`NEW_JOB` must not exist and its parent must exist. The helper produces `inputs.json`, `source_manifest.json`, and optional `paper_text/`. For directories it reads in place. For supported archives it extracts under the new job after safety checks. Follow `inputs.json.source.root`, including any stripped single wrapper directory. Files over the hash limit are listed as unhashed, not silently certified. ZIP/TAR extraction rejects unsafe links, path escapes, case collisions, special files and configured size limits. For unsupported archives, supply a pre-extracted directory. Never use this as authorization to install tools or run project setup.

If `paper.pages` is null, inspect the PDF with an existing reader and run `record_pdf_metadata.py --inputs NEW_JOB/inputs.json --pages COUNT --method READER_USED` with the actual physical page count. It creates `pdf_inspection.json` bound to the PDF hash and preserves original `inputs.json`. Do not guess or backfill a fictional reader. Scoring requires a known positive count so source page references can be range-checked.

## Authoring `audit.json`

Use relative POSIX paths for `inputs_file`, `plan.file`, source evidence and report figures. Source paths are relative to `inputs.json.source.root`; artifact paths are relative to the audit JSON directory. References cannot escape these roots. Source evidence must be UTF-8 text, at most4MiB, with a recorded matching SHA256. Every `matched`, `partial` or `mismatched` claim requires actual code/caller evidence, not just a prose assertion.

The following illustrates the structure of **one** checklist item, not a completed or measured audit. A valid report must contain all six category IDs from `scoring.md`, with genuine paper-derived requirements. Do not copy this illustrative statement as a research result.

```json
{
  "schema_version": "1.0",
  "language": "zh-CN",
  "title": "Paper title — implementation audit",
  "summary": "A conclusion supported by the reviewed evidence.",
  "inputs_file": "inputs.json",
  "runtime_validation": "Not run. Static review only.",
  "plan": {
    "locked_before_code_review": true,
    "file": "plan.json",
    "notes": "Checklist derived from the paper before tracing source implementations."
  },
  "claims": [
    {
      "id": "ARCH-01",
      "category": "architecture",
      "requirement": "The model processes both modalities through independent branches.",
      "status": "matched",
      "critical": true,
      "paper": {
        "pages": [5, 6],
        "section": "3.1 / Figure 2",
        "statement": "Paraphrase the actual paper requirement here."
      },
      "code": [
        {
          "path": "src/model.py",
          "start_line": 20,
          "end_line": 35,
          "note": "Explain the actual active entry/call and the relevant computation."
        }
      ],
      "analysis": "State what corresponds, what differs, and the limits of the evidence."
    }
  ],
  "pipeline": [
    {"title": "Two inputs", "detail": "Explain the actual flow.", "claim_ids": ["ARCH-01"]}
  ],
  "notes": ["Actual runtime and paper-metric reproduction were not verified."],
  "figures": [
    {"path": "figures/paper-figure.png", "caption": "Original paper Figure2, physical PDF page5; cropped only for reading."}
  ]
}
```

### Rules

- `language`: `zh-CN` or `en`; controls generated assessment labels. The template has short bilingual interface labels.
- `plan`: initialize claims as unknown; freeze before source review; then set `locked_before_code_review: true` and `file`. For an honestly retrospective audit use `false`, omit `file`, and explain in `notes`.
- `id`: unique ASCII letters/digits/underscore/hyphen; stable for the whole audit.
- `category`: `architecture`, `modules`, `objectives`, `training`, `data`, `evaluation`.
- `status`: `matched`, `partial`, `mismatched`, `unknown`, `not_applicable`.
- `paper.pages`: physical, one-based PDF pages; nonempty for applicable claims. Optional `section` and `equation` clarify references.
- `critical`: flag indispensable behavior with an unresolved material conflict/unknown; it does not introduce hidden score weights.
- `code`: exact one-based inclusive ranges, at most301 lines per excerpt. Choose narrow informative evidence. The renderer embeds actual file contents and checks their SHA256.
- `not_applicable`: also requires `na_reason`. Paper not disclosing a setting does not make it NA.
- `figures`: optional actual PNG/JPEG artifacts only; embedded into the report. Do not use generated simulated method/experiment images as evidence.
- `pipeline`, `notes`, `figures`: optional; avoid empty invented content just to fill sections.
- Scores and per-claim weights are never supplied manually. Keep factual runtime evidence and logs in the job directory when authorized.

## Freeze, score and render

```text
# Run this before inspecting implementations, while draft statuses are unknown/NA:
python SKILL/scripts/freeze_plan.py NEW_JOB/audit.json --out NEW_JOB/plan.json

# Complete real evidence and assessments, then:
python SKILL/scripts/audit_core.py NEW_JOB/audit.json
python SKILL/scripts/render_report.py --audit NEW_JOB/audit.json --output NEW_JOB/report.html
```

The output is `report.html` plus `report.score.json`. Both refuse overwrite: choose a new filename for revisions. A failed validation produces no invented percentage. The HTML escapes text and source snippets, embeds only validated PNG/JPEG, uses no CDN/network scripts and remains readable when copied alone.
