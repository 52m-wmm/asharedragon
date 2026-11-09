"""Predeclared small linear diagnostic; chronological expanding windows only."""
import math
import numpy as np
import pandas as pd

BASE_FEATURES=('lag_limit_up_count','lag_first_board_count','lag_broken_limit_rate','lag5_mean_up_count')


def finite(value):
    return value is not None and isinstance(value,(int,float,np.number)) and math.isfinite(float(value))


def correlation(x,y,rank=False):
    if len(x)<3:return None
    if rank:x,y=pd.Series(x).rank().to_numpy(),pd.Series(y).rank().to_numpy()
    if np.std(x)==0 or np.std(y)==0:return None
    return float(np.corrcoef(x,y)[0,1])


def ridge_predict(x,y,test,alpha=1.):
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float);test=np.asarray(test,dtype=float)
    mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale==0]=1
    z=(x-mean)/scale;center=y.mean()
    weights=np.linalg.solve(z.T@z+alpha*np.eye(z.shape[1]),z.T@(y-center))
    return float(center+((test-mean)/scale)@weights)


def walk_forward(rows,target,signal='qqq_price_return',min_train=60):
    """Call separately per event provider. Final revised vintages remain unverified.

    Each prediction sees only earlier labels, prior-session A-share features, and
    a US signal whose assumed availability is strictly before the current cutoff.
    """
    ordered=sorted(rows,key=lambda r:r['trade_date'])
    if len({r['trade_date'] for r in ordered})!=len(ordered):raise ValueError('Duplicate experiment dates')
    providers={r['event_provider'] for r in ordered if r.get('event_provider')}
    if len(providers)>1:raise ValueError('Do not pool differing event universes')
    train=[];predictions=[]
    for row in ordered:
        if row.get('lag_event_provider',row.get('event_provider'))!=row.get('event_provider'):continue
        if not all(finite(row.get(c)) for c in (*BASE_FEATURES,signal,target)):continue
        cutoff=row['decision_cutoff_utc']
        if not row['qqq_assumed_available_at_utc']<cutoff:raise ValueError('Future US signal')
        usable=[r for r in train if r['outcome_available_proxy_utc']<cutoff and r['trade_date']<row['trade_date']]
        if len(usable)>=min_train:
            x=[[r[c] for c in BASE_FEATURES] for r in usable];y=[r[target] for r in usable]
            base=ridge_predict(x,y,[row[c] for c in BASE_FEATURES])
            augmented=ridge_predict([a+[r[signal]] for a,r in zip(x,usable)],y,[row[c] for c in BASE_FEATURES]+[row[signal]])
            clip=lambda v: min(1.,max(0.,v)) if target.endswith('_rate') else max(0.,v)
            predictions.append({'trade_date':row['trade_date'],'target':target,'signal':signal,
                'actual':float(row[target]),'a_only':clip(base),'a_plus_qqq':clip(augmented),
                'historical_mean':float(np.mean(y)),'training_rows':len(usable),'last_training_date':usable[-1]['trade_date'],
                'diagnostic_only':True,'pit_certified':False})
        train.append(row)  # current target becomes eligible only for a later prediction
    if not predictions:return {'status':'insufficient_history','eligible_rows':len(train),'oos_rows':0},[]
    actual=np.array([r['actual'] for r in predictions]);base=np.array([r['a_only'] for r in predictions]);aug=np.array([r['a_plus_qqq'] for r in predictions])
    mse_base=float(np.mean((actual-base)**2));mse_aug=float(np.mean((actual-aug)**2))
    summary={'status':'retrospective_diagnostic_only','eligible_rows':len(train),'oos_rows':len(predictions),
        'first_oos':predictions[0]['trade_date'],'last_oos':predictions[-1]['trade_date'],
        'mae_a_only':float(np.mean(abs(actual-base))),'mae_a_plus_qqq':float(np.mean(abs(actual-aug))),
        'mse_a_only':mse_base,'mse_a_plus_qqq':mse_aug,
        'incremental_oos_r2_vs_a_only':1-mse_aug/mse_base if mse_base>0 else None,
        'mae_historical_mean':float(np.mean(abs(actual-np.array([r['historical_mean'] for r in predictions])))),
        'pit_certified':False}
    return summary,predictions
