"""Build isolated components and prepared programs for simulator tests."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile

from api.input_api import InputAPI
from api.common.schema import CanonicalVfInfo
from core.execution.engine import Engine
from core.execution.prepare import prepare_program, ValueStorageLookup
from core.params.database import ParamDB
from core.params.uarch import resolve_uarch
from core.uarch.idu import DispatchBudget

ROOT = Path(__file__).resolve().parents[1]


def component_engine(uarch, db, fallback_dtype="fp32", values=None):
    cfg = resolve_uarch(uarch)
    program = prepare_program(CanonicalVfInfo(context=(), values={}), db, cfg)
    engine = Engine(program, cfg, db, fallback_dtype=fallback_dtype)
    if values is not None:
        engine.registers.value_storage = ValueStorageLookup(values)
        engine.idu.value_storage = engine.registers.value_storage
    return engine


def expanded(vf_info, limit=None):
    db = ParamDB(base_dir=ROOT)
    cfg = resolve_uarch(db.base_uarch, vf_info.uarch)
    if limit is not None:
        cfg = replace(cfg, canonical_dynamic_instruction_limit=limit)
    return prepare_program(vf_info, db, cfg).instructions


def engine_for(vf_info, db=None):
    db = db if db is not None else ParamDB(base_dir=ROOT)
    cfg = resolve_uarch(db.base_uarch, vf_info.uarch)
    return Engine(prepare_program(vf_info, db, cfg), cfg, db)


def parse_vector_body(body):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "predicate.cce"
        path.write_text("void vf(__ubuf__ float *a, __ubuf__ float *out) {"
                        "__VEC_SCOPE__ {vector_f32 x, y; vector_bool mask, p;" + body + "}}")
        return InputAPI.load_cce(path)


def parse_memory_body(body, params="__ubuf__ float *p, __ubuf__ float *q"):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "input.cce"
        path.write_text(f"void vf({params}) {{ __VEC_SCOPE__ {{vector_f32 a, b, c;"
                        "vector_bool mask = pset_b32(PAT_ALL);" + body + "}}")
        return InputAPI.load_cce(path)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dispatch_budget(engine):
    return DispatchBudget(
        engine.registers.get_free_preg(), engine.registers.get_free_predicate(),
        engine.shq.get_free_shq_queue(), engine.lsu.get_free_lsq(), engine.shq.get_free_shq(),
    )
