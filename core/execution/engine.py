from __future__ import annotations
from dataclasses import dataclass, replace
from collections import deque
from core.common.instruction import DynamicInstruction
from core.common.result import SimulationResult
from core.common.values import ValueStorageLookup
from core.params.timing import TimingQuery
from core.uarch.registers import Registers
from core.uarch.shq import SHQ
from core.uarch.exq import EXQ
from core.uarch.exu import EXU
from core.uarch.lsu import LSU
from core.uarch.idu import IDU
from core.uarch.ifu import IFU
from core.execution.loop_control import LoopControl
from core.execution.membar import MembarController, TimedMembarController
from core.execution.scheduler import Scheduler
from core.trace.recorder import TraceRecorder
from core.trace.writer import dump_logs
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

@dataclass(frozen=True)
class DispatchBudget:
    vector: int
    predicate: int
    shq_queue: int
    lsq: int
    shq: int

class Engine:

    def __init__(self, program, uarch, db):
        self.program = program
        self.uarch = uarch
        self.db = db
        self.cycle = 0
        self.last_done_cycle = 0
        self.next_admission = 0
        # Unbounded completion bookkeeping; no ROB capacity or ordered retirement.
        self.active_uops = {}
        self.pipe = deque()
        values = ValueStorageLookup(program.values)
        self.timing = TimingQuery(uarch, db)
        self.registers = Registers(uarch, db, values)
        self.shq = SHQ(uarch)
        self.exq = EXQ(uarch)
        self.exu = EXU(uarch, self.timing)
        self.trace = TraceRecorder(self.registers)
        startup = db.get_defaults().get('vf_startup_cost', 0)
        self.control = TimedMembarController(db, 'fp32', uarch['membar_timing'], issue_floor=startup) if 'membar_timing' in uarch else MembarController(db, 'fp32')
        self.lsu = LSU(uarch, db, self.registers, self.shq, self.timing, self.trace, self.control)
        self.scheduler = Scheduler(uarch, self.registers, self.shq, self.exq, self.exu, self.timing, self.trace)
        self.loops = LoopControl(uarch, program.top_block_loop_bounds, program.empty_top_blocks)
        self.idu = IDU(uarch, db, self.loops, values)
        self.ifu = IFU(program.instructions)
        self.explicit = bool(uarch.get('use_explicit_idu_credit_bank', False))
        self.vector_credit = self.registers.get_free_preg()
        self.predicate_credit = self.registers.get_free_predicate()
        self.shq_credit = self.shq.get_free_shq()
        self.pending_shq = 0
        self.pending_lsq = 0

    def admit(self, inst):
        if not isinstance(inst, DynamicInstruction) or inst.type != 'inst':
            raise TypeError('Engine admits only prepared Canonical instructions')
        profile = self.timing._profile(inst.op, inst.form)
        if profile.op_class != inst.op_class:
            profile = replace(profile, op_class=inst.op_class)
        u = self.registers.rename(inst, profile, self.cycle)
        u.admission_seq = self.next_admission
        self.next_admission += 1
        self.lsu.bind_align_state(u, inst.attributes)
        if profile.op_class in ('LOAD', 'STORE'):
            spec = DEFAULT_INSTRUCTION_CATALOG.lookup(u.op)
            mode = inst.attributes.get('catalog_mode')
            if mode is not None:
                spec = spec.memory_modes.get(mode) if spec else None
            if spec is None or spec.ub_transfer_bytes <= 0:
                raise ValueError('Missing Catalog UB transfer semantics')
            u.ub_transfer_bytes = spec.ub_transfer_bytes
            self.lsu.enqueue(u, self.cycle, self.uarch.get('ooo_to_lsq_delay', 1))
        else:
            self.shq.enqueue(u, self.cycle, self.uarch.get('ooo_to_shq_delay', 1))
        self.shq.reserve(u)
        self.active_uops[u.inst_id] = u
        return u

    def reservation(self, inst):
        values = self.registers.value_storage
        return (
            sum((values.is_register(x) for x in inst.dst)),
            sum((values.is_predicate(x) for x in inst.dst)),
            int(inst.op_class == 'COMPUTE'),
            int(inst.op_class in ('LOAD', 'STORE')),
            int(inst.op_class in ('COMPUTE', 'STORE'))
        )

    def has_pending(self, seq, cls):
        return any((i.stream_seq < seq and i.op_class == cls for i in self.idu.window)) or any((i.stream_seq < seq and i.op_class == cls for _, i in self.pipe)) or self.lsu.has_pending_before(seq, cls)

    def backend_step(self):
        c = self.cycle
        for component in (self.registers, self.trace):
            component.cycle = c
        self.shq.run_shq_release_events(c)
        self.registers.run_src_release_events(c)
        # Keep trace order stable for simultaneous completion events.
        completed = sorted([*self.exu.finish(c), *self.lsu.finish(c)], key=lambda u: u.admission_seq)
        for u in completed:
            self.trace._log('done', u)
            self.trace._log_done_simple(u)
            self.last_done_cycle = max(self.last_done_cycle, u.done_cycle)
            self.registers.on_uop_done(u)
            self.registers.try_free_dests(u)
            self.active_uops.pop(u.inst_id)
        self.registers.try_free_eligible_pregs()
        self.lsu.update_ready(c)

        def ready_compute():
            for u in self.shq.SHQ:
                u.ready_cycle = self.registers._compute_ready_cycle(u)
                u.state = 'ready' if c >= u.ready_cycle else 'blocked'
        ready_compute()
        blocked = set()
        counts = self.lsu._issue_ready_lsu(c, 0, 0, 0, blocked)
        ready_compute()
        self.lsu.update_ready(c, stores_only=True)
        used = [False] * self.exu.issue_ports
        if self.uarch.get('enable_isu_queue_model', True):
            self.scheduler.enqueue_shq_to_exq(c, set())
            self.scheduler.issue_exq_to_exu(c, used)
        else:
            self.scheduler.issue_direct_from_shq(c, set(), used)
        self.lsu.update_ready(c, stores_only=True)
        self.lsu._issue_ready_lsu(c, *counts, blocked)

    def run(self, out_dir):
        delay = int(self.uarch.get('idu_to_ooo_delay', 0))
        max_cycles = self.program.params.get('max_cycles', 1000000)
        completed = False
        while self.cycle < max_cycles:
            c = self.cycle
            self.registers.cycle = c
            self.trace.cycle = c
            delta = self.registers.update_visibility(c)
            shq_delta = self.shq.update_visibility(c)
            if self.explicit:
                self.vector_credit += delta['preg_free']
                self.predicate_credit += delta['predicate_free']
                self.shq_credit += shq_delta
            while self.pipe and self.pipe[0][0] <= c:
                _, inst = self.pipe.popleft()
                if self.explicit:
                    r = self.reservation(inst)
                    self.pending_shq -= r[2]
                    self.pending_lsq -= r[3]
                self.admit(inst)
            pending = [0] * 5
            if not self.explicit:
                for _, inst in self.pipe:
                    for index, count in enumerate(self.reservation(inst)):
                        pending[index] += count
            while self.idu.can_accept() and (not self.ifu.done()):
                inst = self.ifu.next_inst()
                if inst.type == 'membar':
                    self.control.accept_membar(inst, c)
                    continue
                self.idu.accept(inst)
                self.control.observe_instruction(inst)
            self.control.update(
                self.has_pending,
                cycle=c,
                has_pending_dispatch=lambda seq: any((i.stream_seq < seq for i in self.idu.window)) or any((i.stream_seq < seq for _, i in self.pipe))
            )
            budget = DispatchBudget(
                self.vector_credit if self.explicit else max(0, self.registers.get_free_preg() - pending[0]),
                self.predicate_credit if self.explicit else max(0, self.registers.get_free_predicate() - pending[1]),
                max(0, self.shq.get_free_shq_queue() - (self.pending_shq if self.explicit else pending[2])),
                max(0, self.lsu.get_free_lsq() - (self.pending_lsq if self.explicit else pending[3])),
                self.shq_credit if self.explicit else max(0, self.shq.get_free_shq() - pending[4])
            )
            for inst in self.idu.dispatch(c, budget):
                if self.explicit:
                    r = self.reservation(inst)
                    self.vector_credit -= r[0]
                    self.predicate_credit -= r[1]
                    self.shq_credit -= r[4]
                    if delay > 0:
                        self.pending_shq += r[2]
                        self.pending_lsq += r[3]
                if delay > 0:
                    self.pipe.append((c + delay, inst))
                else:
                    self.admit(inst)
            self.backend_step()
            self.last_done_cycle = max(
                self.last_done_cycle,
                self.control.last_retire_cycle if hasattr(self.control, 'last_retire_cycle') else 0
            )
            if self.ifu.done() and self.idu.empty() and (not self.active_uops) and (not self.pipe) and self.control.empty():
                completed = True
                break
            self.cycle += 1
        if not completed:
            raise RuntimeError(f'Simulation did not complete before max_cycles: cycle={self.cycle}')
        trace_path = dump_logs(self, out_dir) if out_dir is not None else None
        return SimulationResult(
            self.cycle,
            self.last_done_cycle + self.db.get_defaults().get('vf_drain_cost', 0),
            str(out_dir) if out_dir is not None else None,
            trace_path
        )
