# Paper–Code Audit for Codex

Turn a **paper PDF + source directory or archive** into a readable, offline HTML implementation review. Codex performs the semantic comparison; bundled Python helpers prepare inputs, validate evidence, calculate a transparent score and render the report.

**论文 PDF + 代码目录 / 压缩包 → 可视化 HTML 一致性报告。** 支持真实调用链、论文页码、源码行号、差异说明、评分和可展开证据。无需上传模型权重，也不会自动训练。

The report shows the paper's main network architecture as one crop from the actual PDF, preserving its labels and legend without surrounding paper text. Each evidence item pairs the verdict, paper paraphrase and code mapping on the left with real source excerpts on the right; narrow screens stack the panels. A light/dark switch in the upper-right remembers the reader's choice. Full paper pages are read internally, not embedded in the report.

Source panels use a distinct editor background, file tabs, line numbers and offline VS Code-like syntax colors. Python and common configuration/code formats are highlighted without extra packages; original source characters and evidence hashes remain unchanged.

## Install

Copy `skills/paper-code-audit` into your Codex skills directory. This package does not require a remote service or API key.

macOS / Linux, from the downloaded repository:

```bash
mkdir -p ~/.codex/skills
cp -R skills/paper-code-audit ~/.codex/skills/
```

Windows PowerShell, from the downloaded repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE/.codex/skills" | Out-Null
Copy-Item -Recurse ./skills/paper-code-audit "$env:USERPROFILE/.codex/skills/"
```

If a skill with the same name is already installed, inspect it before replacing it.

## Use in Codex

```text
Use $paper-code-audit.
Paper: /path/to/paper.pdf
Code: /path/to/source.zip
Output: /path/to/new-audit-directory
Compare the paper's actual method and training/evaluation protocol with the
active source implementation. Produce an evidence-linked offline HTML report.
```

中文示例：

```text
使用 $paper-code-audit。
论文：D:/papers/paper.pdf
代码：D:/downloads/source（也可以是 source.zip）
输出：D:/reports/new-paper-audit
请检查论文方法与真实代码的一致性，给我可视化 HTML，不启动训练。
```

Supported archives: ZIP, TAR, TAR.GZ/TGZ, TAR.BZ2, TAR.XZ. For RAR/7z, supply an extracted directory or convert to a supported archive. Originals stay read-only; the output directory must be new.

## Headline score

| Paper–code consistency | Label |
|---|---|
| Greater than80% | 可复现性强 / Strong reproducibility potential |
| 50%–80%, inclusive | 中等 / Moderate reproducibility potential |
| Below50% | 可能无法正常复现 / May not reproduce as expected |

**These are static-review tiers, not proof of reproduced results.** The report explicitly shows evidence coverage, unresolved critical requirements, runtime-validation status and the unknown-case upper bound. Unknowns remain in the denominator. Missing information can lower the score without proving the code is wrong.

Six fixed weights: architecture25%, core modules25%, objectives/gradients15%, training15%, data10%, evaluation10%. Matched=1; partial=.5; mismatch/unknown=0. The checklist is frozen before source review to reduce score selection. [Full rubric](skills/paper-code-audit/references/scoring.md).

## Contents and requirements

- `skills/paper-code-audit/SKILL.md`: Codex workflow and task boundaries.
- `scripts/prepare_inputs.py`: safe input preparation, source hashes, optional PDF text.
- `scripts/freeze_plan.py`: paper-derived checklist lock.
- `scripts/record_pdf_metadata.py`: records observed page counts after fallback PDF reading.
- `scripts/audit_core.py`: evidence validation and deterministic scoring.
- `scripts/render_report.py` + `assets/report.html`: offline report generation.
- `scripts/syntax_highlight.py`: dependency-free, escaped lexical highlighting for source excerpts.
- `references/`: scoring and JSON format.
- `tests/`: standard-library regression tests.

Python3.10+ runs the helpers. Existing `pdftotext` or `pypdf` can extract text; otherwise Codex should use its available PDF-reading tools. No dependency installation, project-code execution, training, weight download or GitHub publishing is automatic. Report visual QA uses an already available browser tool when possible.

The renderer is **not** a standalone automatic paper-understanding model. It expects an evidence audit authored by Codex. See the [JSON contract](skills/paper-code-audit/references/audit-schema.md) for direct helper usage.

```bash
python -m unittest discover -s tests -v
```

No third-party paper, source snapshot, dataset, checkpoint or private audit is bundled. MIT license applies to this skill's own files; reviewed materials retain their original terms.
