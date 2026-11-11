"""R1: install an accepted release into final snapshot directories, never active.

U data is copied byte-for-byte; its DuckDB catalogue is rebound to final paths.
The existing downstream builders recompute with explicit candidate selectors.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import duckdb
from scripts.finalize_research import dump,sha,literal
from scripts.build_market_breadth import candidate_manifests
from scripts.prepare_source_release import STAGES,build_h,build_e1,build_e2,build_e3,build_d1
from app.research.unified import UnifiedResearch

LAYERS={'U':'research/unified',**STAGES}


def install(accepted_root,root,bundle,lifecycle):
    accepted_root,root,bundle=map(lambda p:Path(p).resolve(),(accepted_root,root,bundle))
    if bundle.exists():raise ValueError('Installation bundle must be new')
    if not bundle.is_relative_to(root/'releases'):raise ValueError('Bundle must be under releases, not an active dataset')
    inventory=json.loads((accepted_root/'release_inventory.json').read_text())
    source=Path(inventory['U']).resolve();source_manifest=json.loads((source/'manifest.json').read_text())
    for name,h in source_manifest['files'].items():
        p=(source/name).resolve()
        if not p.is_relative_to(source) or sha(p)!=h:raise ValueError('Accepted U hash mismatch: '+name)
    identity='r1-'+source.name+'-'+sha(source/'manifest.json')[:8]
    target=root/'research/unified/snapshots'/identity
    if target.exists():raise ValueError('Installed snapshot already exists; never overwrite')
    # Freeze production selectors before any installation. Their bytes are also
    # the executable rollback payload; they are not changed by this command.
    before={k:sha(root/rel/'manifest.json') for k,rel in LAYERS.items()}
    bundle.mkdir(parents=True);(bundle/'manifests').mkdir();(bundle/'rollback').mkdir()
    selectors={root/rel:bundle/'manifests'/(k+'.json') for k,rel in LAYERS.items()}
    for k,rel in LAYERS.items():shutil.copy2(root/rel/'manifest.json',bundle/'rollback'/(k+'.json'))
    def intact():
        if any(sha(root/rel/'manifest.json')!=before[k] for k,rel in LAYERS.items()):raise ValueError('Production selector drift')
    print('INSTALL U',flush=True)
    shutil.copytree(source,target,ignore=shutil.ignore_patterns('research.duckdb','manifest.json','tmp'))
    with duckdb.connect(str(target/'research.duckdb')) as db:
        for p in sorted(target.glob('*.parquet')):
            db.execute(f'CREATE VIEW "{p.stem}" AS SELECT * FROM read_parquet({literal(p)})')
        db.execute(f"CREATE VIEW stock_daily AS SELECT * FROM read_parquet({literal(target/'stock_daily/**/*.parquet')},hive_partitioning=true)")
    dump(target/'installation.json',{'rule':'final-path-install-v1','accepted_source_snapshot':str(source),
      'accepted_manifest_sha256':sha(source/'manifest.json'),'installed_at_utc':datetime.now(timezone.utc).isoformat(),
      'data_policy':'Parquet bytes unchanged; catalogue rebound to installed paths; no active publication'})
    m={**source_manifest,'dataset':str(target.relative_to(root/'research/unified')),
       'files':{str(p.relative_to(target)):sha(p) for p in sorted(target.rglob('*')) if p.is_file()}}
    dump(target/'manifest.json',m);shutil.copy2(target/'manifest.json',selectors[root/'research/unified'])
    with UnifiedResearch(root,dataset=m['dataset']) as reader:
        assert reader.daily(year=2026,month=11,code='600537.SH')['data']
    result={'root':str(root),'bundle':str(bundle),'accepted_root':str(accepted_root),'status':'installing_non_active',
            'versions':{'U':str(target)},'production_before':before,'selectors':{k:str(selectors[root/rel]) for k,rel in LAYERS.items()}}
    dump(bundle/'installation.json',result)
    with candidate_manifests(selectors):
        for layer,runner in [('H',build_h),('E1',lambda r:build_e1(r,lifecycle_path=lifecycle)),('E2',build_e2),('E3',build_e3),('D1',build_d1)]:
            intact();print('INSTALL '+layer,flush=True);runner(root);intact()
            candidate=json.loads(selectors[root/LAYERS[layer]].read_text())
            result['versions'][layer]=str((root/LAYERS[layer]/candidate['dataset']).resolve())
            dump(bundle/'installation.json',result)
    result['status']='installed_pending_R1_acceptance';dump(bundle/'installation.json',result);intact()
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--accepted-root',type=Path,required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--lifecycle',type=Path,required=True)
    a=p.parse_args();install(a.accepted_root,a.root,a.bundle,a.lifecycle)
