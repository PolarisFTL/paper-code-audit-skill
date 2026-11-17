#!/usr/bin/env python3
"""Record an observed PDF page count after fallback reading; never edits the PDF."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from audit_core import AuditError,digest,load_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',required=True,type=Path)
    p.add_argument('--pages',required=True,type=int,help='Physical page count actually observed with a PDF reader')
    p.add_argument('--method',required=True,help='Name the PDF reader/tool actually used')
    args=p.parse_args()
    try:
        d=load_json(args.inputs)
        if d.get('schema_version')!='1.0' or args.pages<1 or not args.method.strip():
            raise AuditError('Require valid prepared inputs, positive page count and actual inspection method')
        paper=Path(d['paper']['path'])
        if digest(paper)!=d['paper']['sha256']:
            raise AuditError('The paper changed after preparation')
        if d['paper'].get('pages') is not None and d['paper']['pages']!=args.pages:
            raise AuditError('Page count conflicts with input preparation; inspect before proceeding')
        out=args.inputs.resolve().parent/'pdf_inspection.json'
        record={'schema_version':'1.0','paper_sha256':d['paper']['sha256'],'pages':args.pages,
                'method':args.method,'recorded_utc':datetime.now(timezone.utc).isoformat(),
                'note':'Reader-observed metadata; not runtime or benchmark evidence. Original inputs.json is unchanged.'}
        with out.open('x',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
        print(json.dumps({'inspection':str(out),'pages':args.pages},ensure_ascii=True))
    except (ValueError,OSError,KeyError,TypeError) as e:p.exit(2,f'PDF metadata failed: {e}\n')


if __name__=='__main__':main()
