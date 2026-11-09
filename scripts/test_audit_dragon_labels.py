from datetime import date
from audit_dragon_labels import price_runs


def test_case_sensitivity_never_publishes_labels_or_skips_missing_days():
    days=[date(2023,9,27),date(2023,9,28),date(2023,10,9),date(2023,10,10),date(2023,10,11)]
    rows=[{'trade_date':d,'scenario_classification':k} for d,k in zip(days,['not_up','up','unknown','up','not_up'])]
    runs=price_runs(rows)
    assert len(runs)==2
    assert runs[0]['right_boundary'] is None and not runs[1]['left_boundary_known_under_assumptions']
    assert all(r['scenario_only'] and not r['formal_eligible'] for r in runs)
