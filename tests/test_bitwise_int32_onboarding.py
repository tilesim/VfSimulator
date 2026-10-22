import json
from pathlib import Path
from collections import defaultdict

from core.param_db import ParamDB
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG


def test_measured_integer_forms_preserve_native_identity_and_signed_shift():
    root=Path(__file__).resolve().parents[1]
    data=json.loads((root/'tests/fixtures/bitwise_int32/calibration.json').read_text())['cases']
    db=ParamDB(base_dir=str(root));forwarding=defaultdict(list)
    assert len(data)==27
    for row in data:
        assert row['golden']['passed'] and row['golden']['guard_passed']
        op=row['native_op'].removeprefix('RV_')
        form='int32' if op in ('VSHLS','VSHRI') else 'b32'
        profile=db.resolve_inst(op,form,'int32')
        assert set(row['compute_latency'])=={profile.latency}
        assert db.get_forwarding_cycles(op,'VSTS','int32',producer_form=form,consumer_form='int32')==4
        assert set(row['compute_to_store'])=={4}
        forwarding[(op,form)]+=row['load_to_compute']
        if row['case'].endswith('_n64'):
            assert min(row['load_latency'])==db.resolve_inst('VLDS','int32','int32').latency
            assert set(row['store_latency'])=={db.resolve_inst('VSTS','int32','int32').latency}
    for (op,form),observed in forwarding.items():
        assert min(observed)>=db.get_forwarding_cycles('VLDS',op,'int32',producer_form='int32',consumer_form=form)
    assert DEFAULT_INSTRUCTION_CATALOG.lookup('VSHRI').signature=='shift_immediate'
    assert not [w for w in db.get_warnings() if w['kind']=='unsupported_isa_form']
