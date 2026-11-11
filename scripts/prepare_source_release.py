"""Build a complete U and its existing downstream graph in a NEW isolated root.

Only local immutable inputs and SELECT-only PG snapshots; no production pointer
publication. The manifests inside the isolated root are build-input selectors.
"""
import argparse
import json
from pathlib import Path
from scripts.finalize_research import build as build_u, dump, sha
from scripts.build_history_limits import build as build_h
from scripts.build_market_breadth import build as build_e1
from scripts.build_limit_structure import build as build_e2
from scripts.build_direction_signals import build as build_e3
from scripts.build_matched_cohort_breadth import build as build_d1
from app.research.unified import UnifiedResearch

STAGES={'H':'statistics/history_limits','E1':'statistics/market_breadth',
        'E2':'statistics/limit_structure','E3':'statistics/direction_signals',
        'D1':'statistics/matched_cohort_breadth'}


def prepare(source_root,output_root,lifecycle):
    source_root=Path(source_root).resolve();output_root=Path(output_root).resolve()
    if source_root==output_root or output_root.exists():
        raise ValueError('A new isolated output root is required')
    output_root.mkdir(parents=True)
    production={str(source_root/rel/'manifest.json'):sha(source_root/rel/'manifest.json')
                for rel in ['research/unified',*STAGES.values()]}
    dump(output_root/'production_before.json',production)
    print('STAGE U',flush=True)
    u=build_u(source_root,output_root=output_root)
    # This is a private selector for the existing root-aware builders, NOT the
    # production root's active pointer. No alias or symlink to production assets.
    (output_root/'research/unified/manifest.json').write_bytes((u/'manifest.json').read_bytes())
    with UnifiedResearch(output_root,dataset=str(u.relative_to(output_root/'research/unified'))) as reader:
        result=reader.daily(year=2026,month=11,code='600537.SH')
        if not result['data']:raise ValueError('Explicit candidate reader produced no sample')
    inventory={'U':str(u),'status':'isolated_candidate','production_active_changed':False}
    dump(output_root/'release_inventory.json',inventory)
    for stage,runner in [('H',build_h),('E1',lambda root:build_e1(root,lifecycle_path=lifecycle)),
                         ('E2',build_e2),('E3',build_e3),('D1',build_d1)]:
        print('STAGE '+stage,flush=True);runner(output_root)
        base=output_root/STAGES[stage];manifest=json.loads((base/'manifest.json').read_text())
        inventory[stage]=str((base/manifest['dataset']).resolve());dump(output_root/'release_inventory.json',inventory)
        for path,digest in production.items():
            if sha(Path(path))!=digest:raise ValueError('Production pointer changed: '+path)
    inventory['status']='built_pending_acceptance';dump(output_root/'release_inventory.json',inventory)
    print(json.dumps(inventory,ensure_ascii=False),flush=True)
    return inventory


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);p.add_argument('--lifecycle',type=Path,required=True)
    a=p.parse_args();prepare(a.source_root,a.output_root,a.lifecycle)
