from api.common.schema import CanonicalVfInfo
from core.execution.prepare import prepare_program
from core.execution.engine import Engine,DispatchBudget
from core.params.database import ParamDB
from core.common.values import ValueStorageLookup
from core.params.uarch import resolve_uarch
from dataclasses import replace


def component_engine(uarch,db,dtype="fp32",values=None):
    vf=CanonicalVfInfo(context=(),values={})
    cfg=resolve_uarch(uarch)
    program=prepare_program(vf,db,cfg)
    engine=Engine(program,cfg,db,fallback_dtype=dtype)
    if values is not None:
        engine.registers.value_storage=ValueStorageLookup(values)
        engine.idu.value_storage=engine.registers.value_storage
    return engine


def expanded(vf_info,limit=None):
    db=ParamDB(base_dir=__import__("pathlib").Path(__file__).resolve().parents[1])
    cfg=resolve_uarch(db.get_uarch())
    if limit is not None:cfg=replace(cfg,canonical_dynamic_instruction_limit=limit)
    return prepare_program(vf_info,db,cfg).instructions


def engine_for(vf_info,db=None):
    db=db or ParamDB(base_dir=__import__("pathlib").Path(__file__).resolve().parents[1])
    cfg=resolve_uarch({**db.get_uarch(),**vf_info.uarch})
    return Engine(prepare_program(vf_info,db,cfg),cfg,db)
