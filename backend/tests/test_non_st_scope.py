from copy import deepcopy
from datetime import date
import pytest
from app.research.non_st_scope import name_exclusion,day_eligibility,aggregate,apply

D=date(2020,12,7);C='600179.SH'
def obs(risk='unknown',name=None):
    return dict(stock_code=C,security_id='sid',valid_from=D,valid_to=D,risk_warning_status=risk,
                historical_name=name,verification_status='source_observed',evidence_id='source:dated',trading_status='normal_trading')
def proof(rate=.1,code=C,day=str(D)):
    return dict(stock_code=code,trade_date=day,verified=True,normal_session=True,not_delisting=True,rate=rate,evidence_ids=['reviewed:regime'])

@pytest.mark.parametrize('name',['ST安','*ST安','XR*ST安','XD*ST莲','DRST安','S*ST安'])
def test_dated_risk_marker_excludes_even_with_ex_prefix(name):
    assert name_exclusion(name)=='risk_warning_confirmed'
    assert day_eligibility(C,D,[obs(name=name)],identity='sid')['status']=='excluded'

def test_delisting_markers_and_unknown_plain_names():
    assert name_exclusion('乐视退')=='delisting_period_confirmed'
    assert name_exclusion('退市保千')=='delisting_period_confirmed'
    assert name_exclusion('普通名称') is None
    assert day_eligibility(C,D,[obs(name='普通名称')],identity='sid')['status']=='unknown'

def test_ordinary_regime_does_not_repeat_non_st_request():
    r=day_eligibility(C,D,[],identity='sid',normal_regime=proof())
    assert r['status']=='eligible' and r['non_st_confirmed']
    assert r['evidence_ids']==['reviewed:regime']

def test_growth_20_and_reformed_main_10_do_not_imply_non_st():
    assert day_eligibility('300001.SZ',D,[],identity='sid',normal_regime=proof(.2,'300001.SZ'))['status']=='unknown'
    assert day_eligibility(C,date(2026,7,6),[],identity='sid',normal_regime=proof(day='2026-07-06'))['status']=='unknown'
    r=day_eligibility(C,D,[obs('none')],identity='sid',normal_regime=proof(.2))
    assert r['status']=='eligible'

def test_direct_event_or_trading_is_not_non_st_or_retirement_evidence():
    assert day_eligibility(C,D,[obs('none')],identity='sid')['status']=='unknown'
    assert 'non_delisting_period_unproven' in day_eligibility(C,D,[obs('none')],identity='sid')['reasons']
    assert day_eligibility(C,date(2020,12,8),[obs('none')],identity='sid')['non_st_confirmed'] is False
    r=day_eligibility(C,D,[obs('st')],identity='sid',normal_regime=proof())
    assert r['status']=='excluded' and r['conflict']

def test_same_identity_does_not_override_code_and_date_scope():
    r=day_eligibility('600186.SH',D,[obs('st')],identity='sid')
    assert r['status']=='unknown' and not r['non_st_confirmed']

def test_eligibility_overlay_is_not_a_market_fact_rewrite():
    rows=[dict(cycle_id=str(i),stock_code=C,outcome='success',observed_height=5,exact_height=5,evidence_grade='verified') for i in range(3)]
    before=deepcopy(rows)
    data={'by_sample':{('direct',str(i)):{'eligibility':{'status':s,'reasons':[]},'cycle_eligibility':{'status':s,'reasons':[]}} for i,s in enumerate(['eligible','unknown','excluded'])},'by_day':{}}
    result,counts=apply(rows,data=data)
    assert [r['outcome'] for r in result]==['success','unknown']
    assert result[1]['historical_outcome']=='success'
    assert counts['excluded']==counts['unknown']==counts['eligible']==1
    assert rows==before

def test_future_exclusion_does_not_erase_prior_qualified_five_board_outcome():
    good={'status':'eligible','reasons':[]};late={'status':'excluded','reasons':['risk_warning_confirmed']}
    assert aggregate([good]*5)['status']=='eligible'
    assert aggregate([good]*5+[late])['status']=='excluded'

def test_api_defaults_to_new_scope_and_requires_explicit_legacy(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.research import seasonality_queries,study_v07_queries,feature_queries
    c=TestClient(app)
    def reply(*args,scope='missing',**kwargs):return {'meta':{'scope':scope}}
    for module,path in [(seasonality_queries,'seasonality-exploratory'),(study_v07_queries,'study-v07'),(feature_queries,'feature-study')]:
        monkeypatch.setattr(module,'query',reply)
        assert c.get('/api/research/dragons/'+path).json()['meta']['scope']=='all'
        assert c.get('/api/research/dragons/'+path+'?scope=legacy').json()['meta']['scope']=='legacy'
        for scope in ('all','confirmed_non_st','confirmed_st','unknown'):
            assert c.get('/api/research/dragons/'+path+'?scope='+scope).json()['meta']['scope']==scope
