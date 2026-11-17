# Evidence-based consistency scoring · v1.0

This is a transparent review rubric, not a statistically calibrated reproducibility predictor. A score cannot certify that the paper's metrics were reproduced. The requested tier names summarize score intervals only.

## Fixed categories

| ID | Weight | Include |
|---|---:|---|
| `architecture` | 25 | Overall stages, branches, backbone/head identity, feature and proposal flow |
| `modules` | 25 | Main innovations, equations, operators, tensor/normalization dimensions |
| `objectives` | 15 | Loss terms, weights/reduction, assignments, gradient/stop-gradient paths |
| `training` | 15 | Initialization, optimizer, learning-rate schedule, budget, training procedures |
| `data` | 10 | Modalities, pairing, preprocessing, annotations, dataset/split identity |
| `evaluation` | 10 | Decoding, thresholds/NMS, metric implementation, precision and eval protocol |

Read the paper first. Establish a small, meaningful requirement inventory before inspecting implementation. Assign each independent requirement to one category once. Do not dilute a major conflict by adding numerous trivial matching operations. A mismatch may affect several observations but receives only its original scoring unit. Category weights remain fixed across audits; scores across unrelated papers still need contextual interpretation.

`freeze_plan.py` records stable IDs, categories, requirement text, applicability and a SHA256 of the checklist. The validator rejects later additions, removals, reordering, requirement edits or switching items to/from NA when the audit claims a frozen plan. The freeze is an audit-trail mechanism, not proof of reviewer independence or trusted chronology. Retrospective use must be declared and is visibly marked.

## Status factors

| Status | Factor | Evidence standard |
|---|---:|---|
| `matched` | 1 | The paper requirement is implemented in the active path |
| `partial` | 0.5 | A specific implemented portion and a specific material deviation are established |
| `mismatched` | 0 | Evidence establishes a contradiction or missing required behavior in the active path |
| `unknown` | 0 | Evidence, disclosure, or execution-path understanding is insufficient |
| `not_applicable` | Excluded | The method genuinely does not involve this requirement; explain with paper-based context |

Uncertainty is not partial credit. Paper-omitted optimizer details, an unlocated implementation, absent runtime testing, and unavailable external weights are not valid reasons to mark a requirement NA. Do not invent undisclosed paper requirements merely to penalize missing information. Record genuine limitations outside the score when there is no concrete paper requirement to judge.

If an entire category is legitimately NA, exclude that category's weight and proportionally normalize the remaining category weights. Display exclusions and reasons. All-NA audits are unscorable and must not produce 100% or any invented percentage.

## Formula

Let `A_g` contain applicable checklist items in category `g`, `w_g` be its fixed category weight, and `s_j` its status factor. Define `G` as categories with at least one applicable item.

```text
category_score_g = sum(s_j for j in A_g) / len(A_g)
score = 100 * sum(w_g * category_score_g for g in G) / sum(w_g for g in G)

category_coverage_g = count(status != unknown in A_g) / len(A_g)
coverage = 100 * weighted_average(category_coverage_g)

unknown_upper = score recalculated with unknown factors set to 1
```

The lower/current score includes unknowns as zero. `unknown_upper` keeps all other judgments unchanged: it is neither a confidence interval nor a prediction. Always `score <= coverage`. Coverage counts judged mismatches as examined evidence; it does not mean those claims match.

Use two-decimal half-up rounding before interval classification so displayed percentages and labels agree:

- `score > 80.00`: 可复现性强 / Strong reproducibility potential.
- `50.00 <= score <= 80.00`: 中等 / Moderate reproducibility potential.
- `score < 50.00`: 可能无法正常复现 / May not reproduce as expected.

Critical unresolved requirements are displayed beside the score without hidden penalties or undocumented score caps. A high score must not hide a known broken entry point, incompatible checkpoint, or fundamentally different evaluation target.

## Evidence levels stay separate

1. Static consistency: paper statements mapped to actual code/config paths.
2. Runtime smoke test: a specific authorized load/forward/loss test passed with logs.
3. Evaluation: actual checkpoint, split and metric run, with provenance.
4. Metric reproduction: full matched protocol and predefined tolerance, supported by actual results.

Only claim the level actually reached. The same static score may coexist with “runtime not tested” or a confirmed runtime blocker. Never invent AP, infer loss from heatmaps, or report incomplete training as a final benchmark.
