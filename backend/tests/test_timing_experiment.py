"""Temporal and statistical guardrails for the preregistered experiment."""
import numpy as np
import pandas as pd
from scripts.run_timing_experiment import future_labels,fit_signals,bootstrap_indices,mean_diff,holm,METRICS,TARGETS


def test_outcome_starts_after_signal_and_requires_full_horizon():
 dates=pd.date_range('2025-12-26',periods=8,freq='B');a=np.arange(8)/10
 out=pd.DataFrame({'intraday_breadth':a,'reference_breadth':a},index=dates)
 r,end=future_labels(out,3)
 assert r[TARGETS[0]][0]==np.mean([.1,.2,.3])
 assert np.isnan(r[TARGETS[0]][-3:]).all()
 assert end[0]==dates[3].to_datetime64()
 altered=out.copy();altered.iloc[0]=999
 assert future_labels(altered,3)[0][TARGETS[0]][0]==r[TARGETS[0]][0]


def test_missing_future_day_is_not_shortened_or_zero_filled():
 out=pd.DataFrame({'intraday_breadth':[.5,.6,np.nan,.8],'reference_breadth':[.5,.6,.7,.8]},index=pd.date_range('2024-01-01',periods=4))
 r,_=future_labels(out,3)
 assert np.isnan(r[TARGETS[0]][0]) and np.isfinite(r[TARGETS[2]][0])


def test_thresholds_never_fit_holdout():
 dates=pd.DatetimeIndex(list(pd.bdate_range('2024-01-02',periods=80))+list(pd.bdate_range('2026-01-05',periods=20)))
 x=np.arange(100,dtype=float);f=pd.DataFrame({k:x for k in METRICS},index=dates)
 a,_,_=fit_signals(f,dates);f.loc[f.index.year==2026]=1e12;b,_,_=fit_signals(f,dates)
 assert a==b and a['up_count']['q75']==np.quantile(np.arange(80),.75)


def test_unavailable_confirmed_history_has_no_zero_threshold():
 dates=pd.bdate_range('2024-01-02',periods=80);f=pd.DataFrame({k:np.nan for k in METRICS},index=dates)
 t,m,e=fit_signals(f,dates)
 assert t['up_count']['q75'] is None and not e['up_count_high'].any() and not m['up_count_high'].any()


def test_block_bootstrap_is_deterministic_and_preserves_local_order():
 a=bootstrap_indices(35,20);assert np.array_equal(a,bootstrap_indices(35,20))
 assert np.all((a[:,1:10]-a[:,:9])%35==1)


def test_nested_baseline_and_multiplicity():
 y=np.array([0.,1.,2.,3.]);a=np.array([False,False,True,True]);b=np.ones(4,dtype=bool)
 r=mean_diff(y,a,b);assert r['conditional_mean']==2.5 and r['baseline_mean']==1.5 and r['effect']==1
 assert np.allclose(holm([.001,.01,.2],3),[.003,.02,.2])
 assert np.isclose(holm([.001],144)[0],.144)


def test_training_labels_cannot_cross_holdout_boundary():
 dates=pd.bdate_range('2025-12-22',periods=15);out=pd.DataFrame({'intraday_breadth':.5,'reference_breadth':.5},index=dates)
 _,ends=future_labels(out,5);train=(dates.year<=2025)&(ends<np.datetime64('2026-01-01'))
 assert all(pd.Timestamp(ends[i]).year==2025 for i in np.flatnonzero(train))
 assert not train[np.where(dates==pd.Timestamp('2025-12-31'))[0][0]]
