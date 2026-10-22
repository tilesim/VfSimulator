from core.ooo import OoOCore
from core.param_db import ParamDB
from pathlib import Path
import json


def test_intervening_alu_does_not_clear_sqrt_unit_initiation_interval():
    db = ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
    core = OoOCore({'issue_ports':1}, db)
    sqrt = db.resolve_inst('VSQRT','fp32','fp32')
    add = db.resolve_inst('VADD','fp32','fp32')
    assert sqrt.initiation_interval_scope=='fu'
    assert db.resolve_inst('VDIV','fp32','fp32').initiation_interval_scope=='exu'
    assert not core.enforce_fu_occupancy
    core.last_op['SFU'][0] = 'VSQRT'
    core.last_form['SFU'][0] = 'fp32'
    core.last_profile['SFU'][0] = sqrt
    core.last_issue_cycle['SFU'][0] = 10
    core.last_op_exu[0] = 'VADD'
    core.last_form_exu[0] = 'fp32'
    core.last_profile_exu[0] = add
    core.last_issue_cycle_exu[0] = 11
    assert core._pick_exu_port('SFU','VSQRT',12,[False],'fp32',sqrt) is None
    assert core._pick_exu_port('SFU','VSQRT',14,[False],'fp32',sqrt) == 0


def test_pending_div_keeps_its_sfu_queue_position_and_waits_for_operands():
    from core.ooo import Uop
    from core.ooo_mainline import OoOCoreMainline
    db = ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
    core = OoOCoreMainline({**db.get_uarch(),'enqueue_pending_sfu':True,'enforce_fu_occupancy':True},db,dtype='fp32')

    def make(ident,op,state='ready',sources=()):
        return Uop(ident,op,'fp32',[],[],list(sources),[],[],
                   profile=db.resolve_inst(op,'fp32','fp32'),state=state)
    sqrt0 = make(1,'VSQRT')
    div0 = make(2,'VDIV','blocked',('p0',))
    sqrt1 = make(3,'VSQRT')
    core.preg_pending.add('p0')
    core.SHQ.extend((sqrt0,div0,sqrt1))
    assert core.isu.enqueue_shq_to_exq(core.vf_startup_cost,set()) == 2
    assert sqrt0.exu_port == 0 and div0.exu_port == 1
    core.isu.issue_exq_to_exu(core.vf_startup_cost+1,[False,False])
    assert div0.state == 'exq_wait'
    assert core.isu.enqueue_shq_to_exq(core.vf_startup_cost+1,set()) == 1
    assert sqrt1.exu_port == 0


def test_profile_scoped_ii_reuses_measured_native_spacing_without_new_cycle_fit():
    root=Path(__file__).resolve().parents[1]
    db=ParamDB(base_dir=str(root))
    records=json.loads((root/'tests/fixtures/sfu_scope/calibration.json').read_text())['cases']
    assert len(records)==8
    for r in records:
        assert r['golden']['passed'] and r['dynamic_counts_match']
        profile=db.resolve_inst(r['op'],r['dtype'],r['dtype'])
        assert profile.initiation_interval_scope=='fu'
        assert r['minimum_spacing_two_starts']==db.get_ii(r['op'],r['op'],r['dtype'])
