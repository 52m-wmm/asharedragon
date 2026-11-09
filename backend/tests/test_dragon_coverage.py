import json
import pytest
from app.research.dragon_coverage import DragonCoverage


def test_expanded_query_separates_unknown_and_formal(tmp_path):
    snapshot=tmp_path/'snapshots'/'one';snapshot.mkdir(parents=True)
    (tmp_path/'manifest.json').write_text(json.dumps({'dataset':'snapshots/one'}))
    (snapshot/'summary.json').write_text(json.dumps({'version':'test','formal_annual_eligible_years':[]}))
    rows=[dict(first_board_year=2021,research_state='diagnostic',max_height=5),dict(first_board_year=2021,research_state='diagnostic',max_height=None),dict(first_board_year=2021,research_state='unknown',max_height=None)]
    rows.append(dict(first_board_year=2021,research_state='verified',max_height=5,formal_eligible=True))
    (snapshot/'cycles.json').write_text(json.dumps(rows))
    d=DragonCoverage(tmp_path)
    assert d.listing()['meta']['total']==0
    result=d.listing(mode='diagnostic',min_height=5)
    assert len(result['data'])==1 and result['meta']['unknown_final_heights']==1
    assert d.listing(mode='unknown')['meta']['total']==1
    assert d.listing(mode='unknown',min_height=5)['meta']['total']==0
    with pytest.raises(ValueError):d.listing(start_year=2026,end_year=2017)


def test_expanded_snapshot_cannot_escape_root(tmp_path):
    (tmp_path/'manifest.json').write_text(json.dumps({'dataset':'../outside'}))
    with pytest.raises(ValueError):DragonCoverage(tmp_path)
