"""Prepared R2/Rollback command. Dry-run by default; R1 must NOT call --apply.

Six existing manifest files are replaced only during a confirmed maintenance
window. No data files are rewritten. Unexpected active drift aborts the operation.
"""
import argparse
import fcntl
import json
from pathlib import Path
from scripts.finalize_research import sha
from scripts.install_source_release import LAYERS


def plan(bundle,direction):
    bundle=Path(bundle).resolve();installation=json.loads((bundle/'installation.json').read_text())
    root=Path(installation['root']);acceptance=json.loads((bundle/'acceptance.json').read_text())
    if not acceptance.get('technical_acceptance_passed'):raise ValueError('R1 technical acceptance missing')
    for name,h in acceptance['installed_files'].items():
        if sha(Path(name))!=h:raise ValueError('Installed content hash mismatch: '+name)
    operations=[]
    for layer,rel in LAYERS.items():
        active=root/rel/'manifest.json';new=bundle/'manifests'/(layer+'.json');old=bundle/'rollback'/(layer+'.json')
        if sha(new)!=acceptance['candidate_manifest_hashes'][layer] or sha(old)!=installation['production_before'][layer]:
            raise ValueError('Candidate/rollback payload changed')
        current=sha(active);oldhash=sha(old);newhash=sha(new)
        if direction=='switch' and current!=oldhash:raise ValueError('Active drift: '+layer)
        # Rollback also recovers a crash halfway through the six replacements.
        if direction=='rollback' and current not in (oldhash,newhash):raise ValueError('Unexpected version during rollback: '+layer)
        operations.append({'layer':layer,'active':str(active),'payload':str(new if direction=='switch' else old),
                           'expected_current':current,'target_hash':newhash if direction=='switch' else oldhash})
    classification=installation.get('classification')
    current_class=root/'statistics/history_limits/classification_manifest.json'
    if classification:
        new=Path(classification['candidate']);old=Path(classification['rollback']);active=current_class
        if sha(new)!=classification['candidate_sha256'] or sha(old)!=classification['before_sha256']:
            raise ValueError('Classification payload changed')
        cm=json.loads(new.read_text());hm=json.loads((bundle/'manifests/H.json').read_text())
        if cm['source_dataset']!=hm['dataset']:raise ValueError('Classification parent mismatch')
        current=sha(active);oldhash=sha(old);newhash=sha(new)
        if direction=='switch' and current!=oldhash:raise ValueError('Classification active drift')
        if direction=='rollback' and current not in (oldhash,newhash):raise ValueError('Unexpected classification version')
        operations.append({'layer':'C','active':str(active),'payload':str(new if direction=='switch' else old),
                           'expected_current':current,'target_hash':newhash if direction=='switch' else oldhash})
    elif current_class.exists():
        cm=json.loads(current_class.read_text());hm=json.loads((bundle/'manifests/H.json').read_text())
        if cm['source_dataset']!=hm['dataset']:
            raise ValueError('Matching classification must join the release/rollback set')
    return {'bundle':str(bundle),'root':str(root),'direction':direction,'operations':operations}


def execute(bundle,direction='switch',*,apply=False,maintenance_confirmed=False):
    if direction not in ('switch','rollback'):raise ValueError('Invalid direction')
    proposed=plan(bundle,direction)
    if not apply:return {'mode':'dry_run','writes':0,**proposed}
    if not maintenance_confirmed:raise ValueError('Maintenance confirmation required; no concurrent readers/builders')
    root=Path(proposed['root'])
    with (root/'releases/switch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        proposed=plan(bundle,direction)  # Check again while owning the lock.
        original={x['active']:Path(x['active']).read_bytes() for x in proposed['operations']}
        try:
            for item in proposed['operations']:
                target=Path(item['active']);temporary=target.with_suffix('.switch.tmp')
                temporary.write_bytes(Path(item['payload']).read_bytes());temporary.replace(target)
            for item in proposed['operations']:
                if sha(Path(item['active']))!=item['target_hash']:raise ValueError('Post-write verification failed')
        except BaseException:
            for name,content in original.items():
                target=Path(name);temporary=target.with_suffix('.rollback.tmp');temporary.write_bytes(content);temporary.replace(target)
            raise
    return {'mode':'applied',**proposed}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--direction',choices=['switch','rollback'],default='switch');p.add_argument('--apply',action='store_true')
    p.add_argument('--maintenance-confirmed',action='store_true');a=p.parse_args()
    print(json.dumps(execute(a.bundle,a.direction,apply=a.apply,maintenance_confirmed=a.maintenance_confirmed),ensure_ascii=False,indent=2))
