import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'skills'/'paper-code-audit'/'scripts'
sys.path.insert(0,str(SCRIPTS))
from audit_core import AuditError,CATEGORIES,calculate,grade,plan_hash,plan_items,read_audit
from render_report import build


def claims(status='matched'):
    return [{'id':k,'category':k,'requirement':'Independent requirement '+k,'status':status,
             'paper':{'pages':[1],'statement':'Fixture requirement '+k},
             'code':[{'path':'model.py','start_line':1,'end_line':2}],
             'analysis':'Software-test fixture, not a research finding.'} for k in CATEGORIES]


class ScoreTests(unittest.TestCase):
    def test_all_match(self):
        s=calculate(claims());self.assertEqual((s['score_percent'],s['coverage_percent'],s['grade']),(100,100,'high'))

    def test_unknown_is_not_success(self):
        s=calculate(claims('unknown'));self.assertEqual((s['score_percent'],s['coverage_percent'],s['unknown_upper_percent']),(0,0,100))

    def test_partial_is_not_unknown(self):
        s=calculate(claims('partial'));self.assertEqual((s['score_percent'],s['coverage_percent'],s['grade']),(50,100,'medium'))

    def test_mismatch_is_examined(self):
        s=calculate(claims('mismatched'));self.assertEqual((s['score_percent'],s['coverage_percent']),(0,100))

    def test_half_weight_unknown_keeps_denominator(self):
        cs=claims('unknown');cs[0]['status']=cs[1]['status']='matched'
        s=calculate(cs);self.assertEqual((s['score_percent'],s['coverage_percent']),(50,50))

    def test_exact_tier_boundaries(self):
        self.assertEqual([grade(x)[0] for x in [49.99,50,80,80.01]],['low','medium','medium','high'])

    def test_na_category_is_explicitly_excluded(self):
        cs=claims();cs[0]['status']='not_applicable'
        s=calculate(cs);self.assertEqual(s['score_percent'],100);self.assertEqual(s['applicable_category_weight'],75)

    def test_all_na_cannot_be_scored(self):
        with self.assertRaises(AuditError):calculate(claims('not_applicable'))

    def test_missing_category_cannot_hide_unknown(self):
        with self.assertRaises(AuditError):calculate(claims()[:-1])

    def test_evidence_score_never_exceeds_coverage(self):
        for statuses in [('matched','partial','unknown','mismatched','matched','unknown'),
                         ('partial',)*6,('mismatched',)*6]:
            cs=claims()
            for c,st in zip(cs,statuses):c['status']=st
            s=calculate(cs);self.assertLessEqual(s['score_percent'],s['coverage_percent'])


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.src=self.root/'source';self.src.mkdir()
        self.code=self.src/'model.py';self.code.write_text('def forward(x):\n    return x\n',encoding='utf-8')
        self.pdf=self.root/'fixture.pdf';self.pdf.write_bytes(b'%PDF-1.4\nSoftware test fixture\n')
        sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        self.write('inputs.json',{'schema_version':'1.0','paper':{'path':str(self.pdf),'sha256':sha(self.pdf),'pages':1},
                                'source':{'kind':'directory','path':str(self.src),'root':str(self.src),'sha256':None},
                                'inventory':{'files':1,'excluded':[]}})
        self.write('source_manifest.json',{'files':[{'path':'model.py','bytes':self.code.stat().st_size,'sha256':sha(self.code)}]})
        self.audit={'schema_version':'1.0','title':'Fixture','summary':'A software-test fixture.',
                    'language':'zh-CN','inputs_file':'inputs.json','runtime_validation':'Not run.',
                    'plan':{'locked_before_code_review':False,'notes':'Fixture retrospective review.'},'claims':claims()}
        self.path=self.root/'audit.json';self.save()

    def write(self,name,obj):(self.root/name).write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    def save(self):self.write('audit.json',self.audit)

    def test_actual_source_evidence_loads(self):
        _,_,score,cache=read_audit(self.path);self.assertEqual(score['score_percent'],100);self.assertEqual(len(cache),1)

    def test_duplicate_requirement_rejected(self):
        self.audit['claims'][1]['requirement']=self.audit['claims'][0]['requirement'];self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_unjustified_na_rejected(self):
        self.audit['claims'][0]['status']='not_applicable';self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_unknown_requires_paper_context(self):
        self.audit['claims'][0]['status']='unknown';self.audit['claims'][0]['paper']['pages']=[];self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_unknown_page_count_requires_inspection(self):
        d=json.loads((self.root/'inputs.json').read_text());d['paper']['pages']=None;self.write('inputs.json',d)
        with self.assertRaises(AuditError):read_audit(self.path)
        self.write('pdf_inspection.json',{'paper_sha256':d['paper']['sha256'],'pages':1,'method':'Fixture reader'})
        read_audit(self.path)

    def test_source_mutation_rejected(self):
        self.code.write_text('changed\n',encoding='utf-8')
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_paper_mutation_rejected(self):
        self.pdf.write_bytes(b'%PDF-changed')
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_line_out_of_bounds_rejected(self):
        self.audit['claims'][0]['code'][0]['end_line']=999;self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_path_escape_rejected(self):
        self.audit['claims'][0]['code'][0]['path']='../fixture.pdf';self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_frozen_denominator_cannot_change(self):
        items=plan_items(self.audit['claims']);self.write('plan.json',{'claims':items,'sha256':plan_hash(items)})
        self.audit['plan']={'locked_before_code_review':True,'file':'plan.json','notes':'Fixture frozen.'};self.save()
        read_audit(self.path)
        self.audit['claims'][0]['requirement']='Replacement requirement';self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_frozen_applicability_cannot_change(self):
        items=plan_items(self.audit['claims']);self.write('plan.json',{'claims':items,'sha256':plan_hash(items)})
        self.audit['plan']={'locked_before_code_review':True,'file':'plan.json','notes':'Fixture frozen.'}
        self.audit['claims'][0]['status']='not_applicable';self.audit['claims'][0]['na_reason']='Changed after review.';self.save()
        with self.assertRaises(AuditError):read_audit(self.path)

    def test_renderer_escapes_untrusted_text_and_exposes_critical(self):
        self.audit['title']='<script>alert(1)</script>'
        self.audit['claims'][0]['critical']=True;self.audit['claims'][0]['status']='mismatched';self.save()
        output,score=build(self.path)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',output)
        self.assertNotIn('<script>alert(1)</script>',output)
        self.assertIn('architecture',score['critical_unresolved_ids'])
        self.assertNotIn('@@SCORE@@',output)
        self.assertIn(f'{score["score_percent"]:.2f}',output)

    def test_renderer_preserves_existing_output(self):
        target=self.root/'report.html'
        command=[sys.executable,str(SCRIPTS/'render_report.py'),'--audit',str(self.path),'--output',str(target)]
        first=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',cwd=SCRIPTS)
        self.assertEqual(first.returncode,0,first.stderr)
        original=target.read_bytes()
        second=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',cwd=SCRIPTS)
        self.assertNotEqual(second.returncode,0);self.assertEqual(target.read_bytes(),original)

    def test_renderer_cannot_write_into_source(self):
        target=self.src/'forbidden.html'
        result=subprocess.run([sys.executable,str(SCRIPTS/'render_report.py'),'--audit',str(self.path),
                               '--output',str(target)],capture_output=True,text=True,encoding='utf-8',errors='replace',cwd=SCRIPTS)
        self.assertNotEqual(result.returncode,0);self.assertFalse(target.exists())


if __name__=='__main__':unittest.main()
