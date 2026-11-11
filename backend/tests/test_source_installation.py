import json
from pathlib import Path
import pytest
from scripts.build_market_breadth import candidate_manifests,manifest_path,save,snapshot
from scripts.switch_source_release import execute
from scripts.install_source_release import LAYERS
from scripts.finalize_research import sha


def test_candidate_selectors_never_write_active(tmp_path):
    base=tmp_path/'research/unified';base.mkdir(parents=True)
    (base/'manifest.json').write_text('old')
    candidate=tmp_path/'candidate.json'
    with candidate_manifests({base:candidate}):
        save(manifest_path(base),{'dataset':'snapshots/new'})
        assert snapshot(base)[0]==base/'snapshots/new'
        with pytest.raises(ValueError,match='Unregistered'):manifest_path(tmp_path/'unregistered')
    assert (base/'manifest.json').read_text()=='old'
    assert manifest_path(base)==base/'manifest.json'


def test_candidate_context_restores_on_error_and_forbids_active(tmp_path):
    with pytest.raises(ValueError,match='cannot be active'):
        with candidate_manifests({tmp_path:tmp_path/'manifest.json'}):pass
    with pytest.raises(RuntimeError):
        with candidate_manifests({tmp_path:tmp_path/'other.json'}):raise RuntimeError()
    assert manifest_path(tmp_path)==tmp_path/'manifest.json'


@pytest.fixture
def bundle(tmp_path):
    b=tmp_path/'releases/test';(b/'manifests').mkdir(parents=True);(b/'rollback').mkdir()
    before={};after={}
    for layer,rel in LAYERS.items():
        active=tmp_path/rel/'manifest.json';active.parent.mkdir(parents=True,exist_ok=True)
        active.write_text(json.dumps({'dataset':'snapshots/old-'+layer}))
        old=b/'rollback'/(layer+'.json');old.write_bytes(active.read_bytes());before[layer]=sha(old)
        new=b/'manifests'/(layer+'.json');new.write_text(json.dumps({'dataset':'snapshots/new-'+layer}));after[layer]=sha(new)
    (b/'installation.json').write_text(json.dumps({'root':str(tmp_path),'production_before':before}))
    (b/'acceptance.json').write_text(json.dumps({'technical_acceptance_passed':True,'installed_files':{},'candidate_manifest_hashes':after}))
    return b


def test_switch_dry_run_is_read_only(bundle):
    result=execute(bundle);assert result['mode']=='dry_run' and result['writes']==0
    assert len(result['operations'])==6
    for x in result['operations']:assert sha(Path(x['active']))==x['expected_current']


def test_apply_requires_maintenance(bundle):
    with pytest.raises(ValueError,match='Maintenance'):execute(bundle,apply=True)


def test_synthetic_switch_and_rollback_roundtrip(bundle):
    # Only pytest tmp_path, never any real data-root manifests.
    execute(bundle,apply=True,maintenance_confirmed=True)
    with pytest.raises(ValueError,match='drift'):execute(bundle)
    execute(bundle,'rollback',apply=True,maintenance_confirmed=True)
    assert execute(bundle)['mode']=='dry_run'


def test_active_drift_rejected(bundle):
    p=Path(execute(bundle)['operations'][0]['active']);p.write_text('unrelated version')
    with pytest.raises(ValueError,match='drift'):execute(bundle)
    with pytest.raises(ValueError,match='Unexpected'):execute(bundle,'rollback')


def test_rollback_recovers_partial_switch(bundle):
    first=execute(bundle)['operations'][0];Path(first['active']).write_bytes(Path(first['payload']).read_bytes())
    execute(bundle,'rollback',apply=True,maintenance_confirmed=True)
    assert execute(bundle)['writes']==0


def test_hash_drift_rejected(bundle):
    (bundle/'manifests/U.json').write_text('tampered')
    with pytest.raises(ValueError,match='payload changed'):execute(bundle)


def classification_bundle(bundle):
    i=json.loads((bundle/'installation.json').read_text());root=Path(i['root'])
    active=root/'statistics/history_limits/classification_manifest.json'
    active.write_text(json.dumps({'dataset':'classifications/old','source_dataset':'snapshots/old-H'}))
    old=bundle/'rollback/C.json';old.write_bytes(active.read_bytes())
    new=bundle/'manifests/C.json';new.write_text(json.dumps({'dataset':'classifications/new','source_dataset':'snapshots/new-H'}))
    i['classification']={'candidate':str(new),'rollback':str(old),'before_sha256':sha(old),'candidate_sha256':sha(new)}
    (bundle/'installation.json').write_text(json.dumps(i))
    return active


def test_seven_pointer_switch_rollback_in_temporary_directory(bundle):
    active=classification_bundle(bundle);before=active.read_bytes()
    assert len(execute(bundle)['operations'])==7
    execute(bundle,apply=True,maintenance_confirmed=True)
    assert json.loads(active.read_text())['source_dataset']=='snapshots/new-H'
    execute(bundle,'rollback',apply=True,maintenance_confirmed=True)
    assert active.read_bytes()==before and execute(bundle)['writes']==0


def test_old_six_pointer_release_cannot_ignore_classification(bundle):
    active=classification_bundle(bundle)
    i=json.loads((bundle/'installation.json').read_text());del i['classification'];(bundle/'installation.json').write_text(json.dumps(i))
    with pytest.raises(ValueError,match='join the release'):execute(bundle)
    assert json.loads(active.read_text())['source_dataset']=='snapshots/old-H'
