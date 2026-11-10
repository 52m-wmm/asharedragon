from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.data_inventory import inventory
router=APIRouter(prefix='/api/data',tags=['data inventory'])
@router.get('/inventory')
def source_inventory(db:Session=Depends(get_db)):
    return inventory(db)

@router.get('/lifecycle')
def lifecycle_review():
    import json
    from pathlib import Path
    from fastapi import HTTPException
    root=Path(__file__).resolve().parents[3]/'data/lifecycle_audit'
    try:
        pointer=json.loads((root/'latest.json').read_text())
        path=(root/pointer['dataset']).resolve()
        if not path.is_relative_to(root.resolve()):raise ValueError('Invalid lifecycle report path')
        return json.loads((path/'report.json').read_text())
    except FileNotFoundError as exc:raise HTTPException(503,'尚未执行上市退市核对') from exc
