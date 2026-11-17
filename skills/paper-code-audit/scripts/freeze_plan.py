#!/usr/bin/env python3
"""Freeze the paper-derived checklist before inspecting source implementations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from audit_core import CATEGORIES, AuditError, load_json, plan_items, plan_hash


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('draft',type=Path,help='Audit draft: scored claims must still be unknown')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    try:
        claims=load_json(args.draft)['claims']
        if not claims or any(c['status'] not in ('unknown','not_applicable') for c in claims):
            raise AuditError('Freeze before code review; scored claims must be unknown')
        if set(c['category'] for c in claims)!=set(CATEGORIES):
            raise AuditError('The plan must cover all six categories')
        items=plan_items(claims)
        if len({c['id'] for c in items})!=len(items):
            raise AuditError('Duplicate plan claim IDs')
        doc={'schema_version':'1.0','created_utc':datetime.now(timezone.utc).isoformat(),
             'claims':items,'sha256':plan_hash(items)}
        with args.out.open('x',encoding='utf-8') as f:
            json.dump(doc,f,ensure_ascii=False,indent=2)
        print(json.dumps({'plan':str(args.out.resolve()),'claims':len(items),'sha256':doc['sha256']}))
    except (ValueError,OSError,KeyError,TypeError) as e:
        p.exit(2,f'Cannot freeze plan: {e}\n')


if __name__=='__main__':main()
