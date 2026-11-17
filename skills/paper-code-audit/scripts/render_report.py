#!/usr/bin/env python3
"""Build an offline HTML report from a validated paper/code evidence audit."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from audit_core import AuditError, read_audit, within

SKILL=Path(__file__).resolve().parent.parent


def esc(x):return html.escape(str(x),quote=True)


def build(audit_path):
    d,inputs,score,source_lines=read_audit(audit_path)
    zh=d.get('language','zh-CN')=='zh-CN'
    labels=({'matched':'匹配','partial':'部分匹配','mismatched':'不一致','unknown':'未验证',
             'not_applicable':'不适用'} if zh else
            {'matched':'Matched','partial':'Partial','mismatched':'Mismatch','unknown':'Unknown',
             'not_applicable':'N/A'})
    cards=[]
    for c in d['claims']:
        p=c['paper'];ev=[]
        for e in c.get('code',[]):
            start,end=e['start_line'],e['end_line']
            indexes=list(range(start-1,end))
            if len(indexes)>75:indexes=indexes[:45]+[None]+indexes[-20:]
            lines=[]
            for i in indexes:
                if i is None:lines.append('      … omitted middle lines …')
                else:lines.append(f'<span class="ln">{i+1:>5}</span> {esc(source_lines[e["path"]][i])}')
            ev.append('<div class="evidence"><div class="code-title"><code>'+esc(e['path'])+
                      f'</code><span>L{start}–{end}</span></div><p>'+esc(e.get('note',''))+
                      '</p><pre><code>'+'\n'.join(lines)+'</code></pre></div>')
        anchor='claim-'+c['id']
        paper_ref=('PDF '+', '.join(str(n) for n in p['pages'])+' · '+p.get('section','')+
                   (' · '+p['equation'] if p.get('equation') else '')).rstrip(' ·')
        na=('<p class="meaning">N/A: '+esc(c['na_reason'])+'</p>') if c['status']=='not_applicable' else ''
        cards.append(f'<article class="audit-card" id="{anchor}" data-status="{c["status"]}" '
                     f'data-search="{esc(c["requirement"]+" "+c["analysis"]+" "+p["statement"])}">'
                     '<div class="card-head"><span class="claim-id">'+esc(c['id'])+'</span><h3>'+esc(c['requirement'])+
                     '</h3><span class="badge '+c['status']+'">'+labels[c['status']]+'</span></div>'
                     '<p class="meaning">'+esc(c['analysis'])+'</p>'+na+
                     '<details><summary>论文与代码证据 / Paper &amp; code evidence</summary>'
                     '<div class="comparison"><div><h4>论文 / Paper</h4><p>'+esc(p['statement'])+
                     '</p><small>'+esc(paper_ref)+'</small></div><div><h4>判定 / Assessment</h4><p>'+
                     esc(c['analysis'])+'</p><small>'+esc(c['category'])+'</small></div></div>'+
                     ''.join(ev)+('' if ev else '<p class="meaning">No source excerpt was verified for this item.</p>')+
                     '</details></article>')
    categories=[]
    for x in score['categories']:
        label=x['label_zh'] if zh else x['label_en']
        display=lambda value: 'N/A' if value is None else f'{value:.2f}%'
        categories.append('<tr><th>'+esc(label)+(f' <small>(N/A {x["not_applicable"]})</small>' if x['not_applicable'] else '')+
                          f'</th><td>{x["weight"]}%</td><td>{display(x["score_percent"])}</td>'+
                          f'<td>{display(x["coverage_percent"])}</td><td>{x["unknown"]} / {x["applicable"]}</td></tr>')
    pipeline=[]
    for i,node in enumerate(d.get('pipeline',[]),1):
        target='#claim-'+node['claim_ids'][0] if node.get('claim_ids') else '#evidence'
        pipeline.append(f'<li><a href="{target}"><span class="step-number">{i:02}</span><h3>'+esc(node['title'])+
                        '</h3><p>'+esc(node['detail'])+'</p></a></li>')
    figures=[]
    for fig in d.get('figures',[]):
        file=within(Path(audit_path).resolve().parent,fig['path'])
        payload=file.read_bytes();mime='image/png' if payload.startswith(b'\x89PNG') else 'image/jpeg'
        url='data:'+mime+';base64,'+base64.b64encode(payload).decode('ascii')
        figures.append('<figure class="paper-image"><img loading="lazy" src="'+url+'" alt="'+esc(fig['caption'])+
                       '"><figcaption>'+esc(fig['caption'])+'</figcaption></figure>')
    common_notes=(['这是静态证据一致性评分，不是运行成功率、校准概率或论文指标复现结论。',
                   '未知项按0分保留分母；低分可能由证据不足导致，须结合覆盖率判断。',
                   '未知项全部匹配时的上限不是置信区间；它仅显示未验证项的评分影响。'] if zh else
                  ['This static evidence score is not a probability of success or proof of reproduced metrics.',
                   'Unknown claims score zero and remain in the denominator; low coverage can cause a low score.',
                   'The unknown upper bound is not a confidence interval.'])
    notes='<ul>'+''.join('<li>'+esc(n)+'</li>' for n in [*common_notes,*d.get('notes',[])])+'</ul>'
    plan=d['plan']
    plan_note=('评分清单在代码核查前已冻结。' if zh else 'Checklist frozen before code review.') if plan['locked_before_code_review'] else (
        '回顾性评分：未在代码审查前冻结清单，存在选择性列项风险。' if zh else
        'Retrospective scoring: checklist was not frozen before code review; selection bias is possible.')
    plan_note+=' '+plan['notes']
    critical=''
    if score['critical_unresolved_ids']:
        critical=('<strong>关键未解决项 / Critical unresolved claims:</strong> '+
                  ', '.join('<a href="#claim-'+cid+'">'+esc(cid)+'</a>' for cid in score['critical_unresolved_ids'])+
                  '<p>高总分不能解除关键冲突；运行可用性仍需单独验证。 / A high score does not resolve blockers.</p>')
    artifact_path=Path(audit_path).resolve()
    sources=('<dl><dt>PDF</dt><dd><code>'+esc(inputs['paper']['path'])+'</code></dd>'+
             '<dt>PDF SHA256</dt><dd><code>'+esc(inputs['paper']['sha256'])+'</code></dd>'+
             '<dt>Physical PDF pages</dt><dd>'+str(inputs['paper']['pages'])+' · '+esc(inputs['paper'].get('page_count_method',inputs.get('pdf_text',{}).get('method','prepared metadata')))+'</dd>'+
             '<dt>Code input</dt><dd><code>'+esc(inputs['source']['path'])+'</code></dd>'+
             '<dt>Effective root</dt><dd><code>'+esc(inputs['source']['root'])+'</code></dd>'+
             '<dt>Runtime validation</dt><dd>'+esc(d['runtime_validation'])+'</dd>'+
             '<dt>Evidence files</dt><dd>'+str(len(source_lines))+' source file SHA256 hashes checked against prepared manifest.</dd>'+
             '<dt>Audit JSON</dt><dd><code>'+esc(artifact_path)+'</code></dd>'+
             '<dt>Scoring version</dt><dd>1.0 · fixed 25/25/15/15/10/10 weights</dd></dl>')
    if inputs['source'].get('sha256'):
        sources+='<p>Archive SHA256: <code>'+esc(inputs['source']['sha256'])+'</code></p>'
    excluded=inputs.get('inventory',{}).get('excluded',[])
    if excluded:
        sources+='<details><summary>Inventory exclusions</summary><pre>'+esc(json.dumps(excluded,ensure_ascii=False,indent=2))+'</pre></details>'
    values={'TITLE':esc(d['title']),'SCORE':f'{score["score_percent"]:.2f}',
            'GRADE':esc(score['grade_zh'] if zh else score['grade_en']), 'GRADE_CLASS':score['grade'],
            'COVERAGE':f'{score["coverage_percent"]:.2f}','UNKNOWN_UPPER':f'{score["unknown_upper_percent"]:.2f}',
            'SUMMARY':esc(d['summary']),'COUNTS':esc(' · '.join(labels[s]+': '+str(n) for s,n in score['status_counts'].items())),
            'CATEGORY_ROWS':''.join(categories),'CARDS':''.join(cards),
            'PIPELINE':('<ol>'+''.join(pipeline)+'</ol>' if pipeline else '')+''.join(figures),
            'NOTES':notes,'PROVENANCE':sources,'DATE':datetime.now(timezone.utc).strftime('%Y-%m-%d UTC'),
            'PLAN_NOTE':esc(plan_note),'CRITICAL':critical,'LANG':esc(d.get('language','zh-CN'))}
    output=(SKILL/'assets'/'report.html').read_text(encoding='utf-8')
    for key,value in values.items():output=output.replace('@@'+key+'@@',value)
    import re
    if re.search(r'@@[A-Z_]+@@',output):raise AuditError('Unresolved HTML template fields')
    return output,score


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path,help='New offline HTML; refuses overwrite')
    args=p.parse_args()
    try:
        artifact_root=args.audit.resolve().parent
        try:args.output.resolve().relative_to(artifact_root)
        except ValueError as e:raise AuditError('Report output must stay inside the audit job directory') from e
        # Extracted source may itself be under the job; keep that snapshot immutable too.
        from audit_core import load_json
        metadata=load_json(within(artifact_root,load_json(args.audit).get('inputs_file','inputs.json')))
        try:args.output.resolve().relative_to(Path(metadata['source']['root']).resolve())
        except ValueError:pass
        else:raise AuditError('Report output must not be inside the source snapshot')
        score_path=args.output.with_suffix('.score.json')
        if args.output.exists() or score_path.exists():
            raise AuditError('Report/score already exists; use a new output filename')
        output,score=build(args.audit)
        with args.output.open('x',encoding='utf-8') as f:f.write(output)
        with score_path.open('x',encoding='utf-8') as f:json.dump(score,f,ensure_ascii=False,indent=2)
        print(json.dumps({'html':str(args.output.resolve()),'score_json':str(score_path.resolve()),
                          'score_percent':score['score_percent'],'grade':score['grade_zh'],
                          'coverage_percent':score['coverage_percent']},ensure_ascii=True))
    except (ValueError,OSError,KeyError,TypeError,UnicodeError) as e:
        p.exit(2,f'Report generation failed: {e}\n')


if __name__=='__main__':main()
