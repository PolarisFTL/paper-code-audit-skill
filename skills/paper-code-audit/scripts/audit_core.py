#!/usr/bin/env python3
"""Evidence validation and deterministic scoring; no project code is imported."""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re

VERSION = '1.0'
CATEGORIES = {
    'architecture': (25, '架构与数据流', 'Architecture and data flow'),
    'modules': (25, '核心模块与公式', 'Core modules and equations'),
    'objectives': (15, '损失与梯度路径', 'Objectives and gradient paths'),
    'training': (15, '训练协议', 'Training protocol'),
    'data': (10, '数据与预处理', 'Data and preprocessing'),
    'evaluation': (10, '推理与评估', 'Inference and evaluation'),
}
POINTS = {'matched': Decimal(1), 'partial': Decimal('.5'),
          'mismatched': Decimal(0), 'unknown': Decimal(0)}
STATUSES = {*POINTS, 'not_applicable'}


class AuditError(ValueError):
    pass


def load_json(path):
    def reject(value):
        raise AuditError(f'Non-finite JSON value: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=reject)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def within(root, relative):
    """Resolve a relative artifact/evidence path without permitting escapes."""
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise AuditError('Use nonempty relative POSIX paths in evidence/artifacts')
    if re.match(r'^[A-Za-z]:', relative) or Path(relative).is_absolute():
        raise AuditError(f'Absolute evidence path is not permitted: {relative}')
    if any(p in ('', '.', '..') for p in relative.split('/')):
        raise AuditError(f'Unsafe relative path: {relative}')
    root = Path(root).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise AuditError(f'Path escapes input root: {relative}') from e
    if not target.is_file():
        raise AuditError(f'Evidence/artifact not found: {relative}')
    return target


def text(value, label, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise AuditError(f'{label} must be a nonempty string')
    return value


def percent(value):
    return float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def grade(score):
    # Classify the same rounded percentage that is displayed to the reader.
    if score > 80:
        return 'high', '可复现性强', 'Strong reproducibility potential'
    if score >= 50:
        return 'medium', '中等', 'Moderate reproducibility potential'
    return 'low', '可能无法正常复现', 'May not reproduce as expected'


def plan_items(claims):
    return [{'id': c['id'], 'category': c['category'], 'requirement': c['requirement'],
             'applicability': 'not_applicable' if c['status']=='not_applicable' else 'applicable'}
            for c in claims]


def plan_hash(items):
    return hashlib.sha256(json.dumps(items,ensure_ascii=False,sort_keys=True,
                                     separators=(',',':')).encode('utf-8')).hexdigest()


def architecture_figure(audit):
    """Only an explicitly selected, cropped main diagram belongs in the report.

    Legacy unclassified figures often contain entire paper pages. They remain
    valid audit data but are deliberately not used as report images.
    """
    primary=audit.get('architecture_figure')
    if primary is not None:
        if not isinstance(primary,dict):raise AuditError('architecture_figure must be an object')
        if primary.get('cropped') is not True:
            raise AuditError('The architecture figure must be cropped to the diagram (cropped=true)')
        if type(primary.get('source_page')) is not int or primary['source_page']<1:
            raise AuditError('architecture_figure.source_page must be a positive physical PDF page')
        return primary
    legacy=audit.get('figures',[])
    if not isinstance(legacy,list):raise AuditError('Legacy figures must be a list')
    selected=[f for f in legacy if isinstance(f,dict) and f.get('role')=='architecture' and f.get('cropped') is True]
    if len(selected)>1:raise AuditError('Select only one cropped main architecture figure')
    return selected[0] if selected else None


def calculate(claims):
    """Unknowns remain in denominator. NA only removes genuinely inapplicable claims."""
    groups = []
    earned = known = optimistic = total_weight = Decimal(0)
    for key, (weight, zh, en) in CATEGORIES.items():
        selected = [c for c in claims if c['category'] == key]
        applicable = [c for c in selected if c['status'] != 'not_applicable']
        n = len(applicable)
        if not selected:
            raise AuditError(f'Missing category: {key}; record unknown/NA explicitly')
        if n:
            w = Decimal(weight)
            merit = sum((POINTS[c['status']] for c in applicable), Decimal(0)) / n
            coverage = Decimal(sum(c['status'] != 'unknown' for c in applicable)) / n
            upper = merit + Decimal(sum(c['status'] == 'unknown' for c in applicable)) / n
            earned += w * merit
            known += w * coverage
            optimistic += w * upper
            total_weight += w
        else:
            merit = coverage = upper = None
        groups.append({'id': key, 'label_zh': zh, 'label_en': en, 'weight': weight,
                       'applicable': n, 'not_applicable': len(selected)-n,
                       'unknown': sum(c['status'] == 'unknown' for c in applicable),
                       'score_percent': percent(merit*100) if n else None,
                       'coverage_percent': percent(coverage*100) if n else None})
    if not total_weight:
        raise AuditError('No applicable claims: do not manufacture a percentage')
    score = percent(earned / total_weight * 100)
    tier, zh, en = grade(score)
    return {'scoring_version': VERSION, 'score_percent': score,
            'grade': tier, 'grade_zh': zh, 'grade_en': en,
            'coverage_percent': percent(known / total_weight * 100),
            'unknown_upper_percent': percent(optimistic / total_weight * 100),
            'applicable_category_weight': float(total_weight),
            'status_counts': dict(Counter(c['status'] for c in claims)),
            'categories': groups,
            'critical_unresolved_ids': [c['id'] for c in claims if c.get('critical')
                                        and c['status'] in ('mismatched','unknown','partial')]}


def read_audit(audit_path):
    audit_path = Path(audit_path).resolve()
    d = load_json(audit_path)
    if not isinstance(d,dict):
        raise AuditError('Audit JSON must be an object')
    if d.get('schema_version') != VERSION:
        raise AuditError('Expected schema_version 1.0')
    for field in ('title','summary','runtime_validation'):
        text(d.get(field), field)
    if d.get('language', 'zh-CN') not in ('zh-CN','en'):
        raise AuditError('language must be zh-CN or en')
    plan = d.get('plan', {})
    if not isinstance(plan.get('locked_before_code_review'), bool):
        raise AuditError('plan.locked_before_code_review must be true or false')
    text(plan.get('notes'), 'plan.notes')
    inputs_path = within(audit_path.parent, d.get('inputs_file','inputs.json'))
    inputs = load_json(inputs_path)
    if inputs.get('schema_version') != VERSION:
        raise AuditError('Input metadata schema must be 1.0')
    source = Path(inputs['source']['root']).resolve()
    if not source.is_dir():
        raise AuditError('Prepared source root is missing')
    paper = Path(inputs['paper']['path'])
    if not paper.is_file() or digest(paper) != inputs['paper']['sha256']:
        raise AuditError('Paper is missing or changed since input preparation')
    count=inputs['paper'].get('pages')
    if type(count) is not int or count<1:
        inspection_path=inputs_path.parent/'pdf_inspection.json'
        if not inspection_path.is_file():
            raise AuditError('Observe the physical PDF page count and run record_pdf_metadata.py before scoring')
        inspection=load_json(inspection_path)
        count=inspection.get('pages')
        if inspection.get('paper_sha256')!=inputs['paper']['sha256'] or type(count) is not int or count<1:
            raise AuditError('PDF inspection metadata is invalid or refers to another PDF')
        text(inspection.get('method'),'PDF inspection method')
        inputs['paper']['pages']=count
        inputs['paper']['page_count_method']=inspection['method']
    manifest_path = inputs_path.parent / 'source_manifest.json'
    manifest = load_json(manifest_path)
    files = {x['path']: x for x in manifest['files']}
    claims = d.get('claims')
    if not isinstance(claims, list) or not claims or len(claims) > 500:
        raise AuditError('claims must contain 1–500 evidence-based requirements')
    ids, requirements, cache = set(), set(), {}
    for c in claims:
        if not isinstance(c,dict):
            raise AuditError('Every claim must be an object')
        cid = text(c.get('id'), 'claim.id')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', cid) or cid in ids:
            raise AuditError(f'Unsafe or duplicate claim id: {cid}')
        ids.add(cid)
        requirement = text(c.get('requirement'), cid+'.requirement')
        normalized = re.sub(r'\W', '', requirement.casefold())
        if normalized in requirements:
            raise AuditError(f'Duplicate requirement: {cid}')
        requirements.add(normalized)
        if c.get('category') not in CATEGORIES or c.get('status') not in STATUSES:
            raise AuditError(f'Invalid category/status for {cid}')
        if 'weight' in c or 'score' in c:
            raise AuditError('Per-claim weights and hand-entered scores are not supported')
        if not isinstance(c.get('critical',False), bool):
            raise AuditError(f'{cid}.critical must be boolean')
        p = c.get('paper', {})
        text(p.get('statement'), cid+'.paper.statement')
        pages = p.get('pages')
        if not isinstance(pages,list) or any(type(n) is not int or n<1 for n in pages):
            raise AuditError(f'Invalid paper pages for {cid}')
        if c['status'] != 'not_applicable' and not pages:
            raise AuditError(f'Physical PDF page references are required for {cid}')
        page_count = inputs['paper'].get('pages')
        if page_count and any(n > page_count for n in pages):
            raise AuditError(f'Paper page out of range for {cid}')
        text(c.get('analysis'), cid+'.analysis')
        evidence = c.get('code', [])
        if not isinstance(evidence,list) or len(evidence)>30:
            raise AuditError(f'Invalid code evidence list for {cid}')
        if c['status'] in ('matched','partial','mismatched') and not evidence:
            raise AuditError(f'{cid}: judged claims need actual source/caller evidence')
        if c['status'] == 'not_applicable':
            text(c.get('na_reason'), cid+'.na_reason')
        for e in evidence:
            relative = e.get('path')
            path = within(source,relative)
            if relative not in files:
                raise AuditError(f'Evidence was not inventoried: {relative}')
            if relative not in cache:
                if path.stat().st_size > 4*1024*1024:
                    raise AuditError(f'Evidence file too large for embedded text: {relative}')
                expected = files[relative].get('sha256')
                if not expected or digest(path)!=expected:
                    raise AuditError(f'Evidence file has no recorded hash or changed: {relative}')
                payload=path.read_bytes()
                if b'\x00' in payload:
                    raise AuditError('Binary evidence cannot be embedded as source text')
                cache[relative]=payload.decode('utf-8-sig').splitlines()
            start,end=e.get('start_line'),e.get('end_line')
            if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(cache[relative]):
                raise AuditError(f'Invalid evidence line range: {cid} {relative}')
            if end-start>300:
                raise AuditError('Keep each evidence range within 301 lines')
    if plan['locked_before_code_review']:
        saved=load_json(within(audit_path.parent,plan.get('file')))
        items=plan_items(claims)
        if saved.get('claims')!=items or saved.get('sha256')!=plan_hash(items):
            raise AuditError('Claim inventory changed after the audit plan was locked')
    for node in d.get('pipeline',[]):
        text(node.get('title'),'pipeline.title');text(node.get('detail'),'pipeline.detail')
        if any(cid not in ids for cid in node.get('claim_ids',[])):
            raise AuditError('Pipeline references an unknown claim id')
    for value in d.get('notes',[]):
        text(value,'note')
    selected_figure=architecture_figure(d)
    for fig in [selected_figure] if selected_figure else []:
        path=within(audit_path.parent,fig.get('path'))
        if path.stat().st_size>12*1024*1024:
            raise AuditError('Figure exceeds the 12 MiB embed limit')
        text(fig.get('caption'),'figure.caption')
        if 'source_page' in fig and (type(fig['source_page']) is not int or not 1<=fig['source_page']<=inputs['paper']['pages']):
            raise AuditError('Architecture figure source_page is outside the physical PDF page range')
        data=path.read_bytes()
        if not (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')):
            raise AuditError('Only actual PNG/JPEG figures may be embedded')
    return d, inputs, calculate(claims), cache


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('audit',type=Path)
    p.add_argument('--out',type=Path,help='Optional new JSON output; refuses overwrite')
    args=p.parse_args()
    try:
        _,_,result,_=read_audit(args.audit)
        payload=json.dumps(result,ensure_ascii=False,indent=2)
        if args.out:
            with args.out.open('x',encoding='utf-8') as f:f.write(payload+'\n')
        print(json.dumps(result,ensure_ascii=True,indent=2))
    except (ValueError,OSError,KeyError,TypeError,UnicodeError) as e:
        p.exit(2,f'Audit validation failed: {e}\n')


if __name__=='__main__':
    main()
