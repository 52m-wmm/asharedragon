import importlib.util
from pathlib import Path
import pytest
from datetime import date

spec=importlib.util.spec_from_file_location('archive_runner',Path(__file__).with_name('archive_tushare.py'))
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def test_archive_hash_detects_corruption_and_retains_revisions(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'BASE',tmp_path/'raw');monkeypatch.setattr(runner,'ROOT',tmp_path)
    path,h=runner.archive('limit_list_d',date(2020,1,2),'U',{'fields':['extra'],'records':[{'extra':42}]},'success','source')
    full=tmp_path/path
    assert runner.verified(full)['response_hash']==h
    runner.archive('limit_list_d',date(2020,1,2),'U',{'records':[]},'empty_unverified','source')
    assert len(list(full.parent.glob('*.json')))==2
    full.write_text(full.read_text().replace('"records": []','"records": [1]'))
    with pytest.raises(RuntimeError,match='hash mismatch'):runner.verified(full)
