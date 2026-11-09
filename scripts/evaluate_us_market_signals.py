"""Descriptive and walk-forward diagnostics; no strategy or causal claims."""
import json
from pathlib import Path
import duckdb
from app.research.us_signals import ROOT,TARGETS
from app.research.us_experiment import correlation,finite,walk_forward,BASE_FEATURES
from build_us_market_signals import save_json,write_records


def evaluate():
    manifest=json.loads((ROOT/'manifest.json').read_text());out=ROOT/manifest['dataset']
    with duckdb.connect() as db:
        table=db.read_parquet(str(out/'aligned_diagnostics.parquet')).to_arrow_table();rows=table.to_pylist()
    providers=sorted({r['event_provider'] for r in rows if r['event_provider']})
    description=[];experiments=[];predictions=[]
    for provider in providers:
        sample=[r for r in rows if r['event_provider']==provider]
        for signal in ('qqq_price_return','qqq_cash_return_proxy'):
            for target in TARGETS:
                comparable=[r for r in sample if finite(r.get(signal)) and finite(r.get(target))]
                x=[r[signal] for r in comparable];y=[r[target] for r in comparable]
                positive=[r[target] for r in comparable if r[signal]>0];negative=[r[target] for r in comparable if r[signal]<0]
                description.append({'provider':provider,'signal':signal,'target':target,'n':len(x),
                    'pearson':correlation(x,y),'spearman':correlation(x,y,rank=True),
                    'mean_target_after_positive_qqq':sum(positive)/len(positive) if positive else None,
                    'mean_target_after_negative_qqq':sum(negative)/len(negative) if negative else None,
                    'positive_n':len(positive),'negative_n':len(negative)})
                summary,preds=walk_forward(sample,target,signal,min_train=60)
                experiments.append({'provider':provider,'signal':signal,'target':target,**summary})
                predictions.extend({'provider':provider,**r} for r in preds)
    audit=json.loads((out/'audit.json').read_text())
    report={'description':description,'experiments':experiments,
        'strict_pit_eligible_rows':audit['strict_eligible_rows'],'strict_prediction_result':'not_estimable_no_historical_vintage_archive',
        'protocol':{'primary_signal':'qqq_price_return','sensitivity_signal':'qqq_cash_return_proxy',
            'base_features':BASE_FEATURES,'minimum_training_rows':60,'ridge_alpha':1.,
            'scaling':'fit on each training window only','split':'expanding chronological; same test rows for paired models',
            'hyperparameter_search':False,'provider_pooling':False,'targets':'same A-share day EOD outcomes; never preopen features',
            'price_gap_fill':False,'interpretation':'retrospective diagnostic only; no causal/profitability/PIT-certified prediction claim',
            'limits':['final historical vintages','small and discontinuous event coverage','2020 regime concentration','multiple exploratory comparisons','unverified historical industry classifications']}}
    save_json(out/'experiments.json',report)
    if predictions:write_records(out/'diagnostic_predictions.parquet',predictions)
    print(out/'experiments.json')
    for r in experiments:
        if r['signal']=='qqq_price_return':print(r)


if __name__=='__main__':evaluate()
