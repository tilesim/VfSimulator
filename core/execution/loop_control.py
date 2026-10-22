from __future__ import annotations
from core.params.types import UarchParameters

class LoopControl:

    def __init__(self, uarch: UarchParameters, top_block_loop_bounds, empty_top_blocks):
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.theoretical_limit_vloop_only = uarch.theoretical_limit_vloop_only
        self.block_base_cycle = {}
        self.innermost_iter_dispatch_stride = uarch.innermost_iter_dispatch_stride
        self.vloop_to_dispatch_delay = uarch.vloop_to_dispatch_delay
        self.initial_top_block_vloop_start_cycle = uarch.initial_top_block_vloop_start_cycle
        self.nested_vloop_initial_start_gap = uarch.nested_vloop_initial_start_gap
        self.loop1_min_feedback_gap = uarch.loop1_min_feedback_gap
        self.total_top_blocks = len(top_block_loop_bounds)
        self.empty_top_blocks = {int(item) for item in empty_top_blocks or ()}
        self.top_block_loop_bounds = {int(k): list(v or []) for k, v in top_block_loop_bounds.items()}
        self.top_block_vloop_start = {}
        self.top_block_body_open_time = {}
        self.vloop_start = {}
        self.body_open_time = {}
        self.last_dispatch_time = {}
        self.vloop_trace = []
        self._init_vloop_starts()

    def _get_block_bounds(self, top_block_id: int):
        return list(self.top_block_loop_bounds.get(int(top_block_id), []))

    def _get_block_depth(self, top_block_id: int) -> int:
        return len(self._get_block_bounds(top_block_id))

    def _set_top_block_vloop(self, top_block_id: int, start_cycle: int):
        top_block_id = int(top_block_id)
        start_cycle = int(start_cycle)
        if top_block_id in self.top_block_vloop_start:
            return
        self.top_block_vloop_start[top_block_id] = start_cycle
        self.top_block_body_open_time[top_block_id] = start_cycle + self.vloop_to_dispatch_delay

    def _record_vloop(self, top_block_id: int, loop_id: str, it, start_cycle: int):
        self.vloop_trace.append({
            'top_block_id': int(top_block_id),
            'loop_id': loop_id,
            'iter': list(it),
            'start_cycle': int(start_cycle)
        })

    def _make_key(self, top_block_id: int, loop_id: str, it) -> tuple:
        return (int(top_block_id), str(loop_id), tuple(it))

    def _set_vloop_start(self, key, start_cycle: int):
        """
        key:
          (tbid, "loop0", ())
          (tbid, "loop1", (i,))
          (tbid, "loop2", (i,j))
        """
        if key in self.vloop_start:
            return
        start_cycle = int(start_cycle)
        self.vloop_start[key] = start_cycle
        self.body_open_time[key] = start_cycle + self.vloop_to_dispatch_delay
        tbid, loop_id, it = key
        self._record_vloop(tbid, loop_id, it, start_cycle)

    def _init_top_block_nested_starts(self, top_block_id: int, top_vloop_start: int):
        """
        When a top-level sibling block is triggered, initialize its first nested VLOOPs.
        Supports depth 1/2/3 independently for each top block.
        """
        tbid = int(top_block_id)
        T = int(top_vloop_start)
        bounds = self._get_block_bounds(tbid)
        depth = len(bounds)
        if depth <= 0:
            return
        self._set_vloop_start(self._make_key(tbid, 'loop0', ()), T)
        if depth >= 2 and bounds[0] > 0:
            self._set_vloop_start(self._make_key(tbid, 'loop1', (0,)), T + self.nested_vloop_initial_start_gap)
        if depth >= 3 and bounds[0] > 0 and (bounds[1] > 0):
            self._set_vloop_start(self._make_key(tbid, 'loop2', (0, 0)), T + 2 * self.nested_vloop_initial_start_gap)

    def _init_vloop_starts(self):
        """
        Initialize top_block 0 only:
          top_block 0 VLOOP = 19
        then initialize its nested starts.
        """
        if self.total_top_blocks <= 0:
            return
        first_top = self._next_nonempty_top_block(0)
        if first_top is None:
            return
        start_cycle = int(self.initial_top_block_vloop_start_cycle)
        self._set_top_block_vloop(first_top, start_cycle)
        self._init_top_block_nested_starts(first_top, start_cycle)

    def _next_nonempty_top_block(self, start: int):
        for top_block_id in range(max(0, int(start)), self.total_top_blocks):
            if top_block_id not in self.empty_top_blocks:
                return top_block_id
        return None

    def _normalize_block_key(self, raw_key, top_block_id: int):
        """
        IFU raw_key:
          ("loop0", ())
          ("loop1", (i,))
          ("loop2", (i,j))
        Here we prepend top_block_id namespace.
        """
        if isinstance(raw_key, (list, tuple)) and len(raw_key) == 2:
            loop_id = raw_key[0]
            it = tuple(raw_key[1]) if isinstance(raw_key[1], (list, tuple)) else ()
            return self._make_key(top_block_id, loop_id, it)
        return None

    def _current_inner_block_key(self, inst):
        """
        Pick the deepest loop block that this instruction actually belongs to.

        A top-level loop body can contain instructions before/after sibling
        inner loops. Those instructions only carry a loop0 key; gating them on
        the top block's maximum depth would incorrectly make them wait for an
        inner-loop VLOOP that has not started yet.
        """
        top_block_id = int(inst.top_block_id)
        depth = self._get_block_depth(top_block_id)
        bk = inst.block_key_by_level
        if isinstance(bk, list) and len(bk) >= 1:
            key = self._normalize_block_key(bk[-1], top_block_id)
            if key is not None:
                return key
        iter_stack = inst.iter_stack
        inst_depth = int(inst.loop_depth)
        active_depth = min(depth, inst_depth)
        if active_depth <= 0:
            return None
        if active_depth == 1:
            return self._make_key(top_block_id, 'loop0', ())
        if active_depth == 2 and len(iter_stack) >= 1:
            return self._make_key(top_block_id, 'loop1', (int(iter_stack[0]),))
        if active_depth == 3 and len(iter_stack) >= 2:
            return self._make_key(top_block_id, 'loop2', (int(iter_stack[0]), int(iter_stack[1])))
        return None

    def _update_last_dispatch(self, inst, cycle: int):
        """
        Record last dispatch time for all nested blocks this inst belongs to.
        """
        top_block_id = int(inst.top_block_id)
        depth = self._get_block_depth(top_block_id)
        bk = inst.block_key_by_level
        if isinstance(bk, list):
            for raw_key in bk:
                key = self._normalize_block_key(raw_key, top_block_id)
                if key is not None:
                    self.last_dispatch_time[key] = int(cycle)
            return
        iter_stack = inst.iter_stack
        if depth == 1:
            self.last_dispatch_time[self._make_key(top_block_id, 'loop0', ())] = int(cycle)
        elif depth == 2 and len(iter_stack) >= 1:
            self.last_dispatch_time[self._make_key(top_block_id, 'loop1', (int(iter_stack[0]),))] = int(cycle)
        elif depth == 3 and len(iter_stack) >= 2:
            self.last_dispatch_time[self._make_key(top_block_id, 'loop2', (int(iter_stack[0]), int(iter_stack[1])))] = int(cycle)
            self.last_dispatch_time[self._make_key(top_block_id, 'loop1', (int(iter_stack[0]),))] = int(cycle)
            self.last_dispatch_time[self._make_key(top_block_id, 'loop0', ())] = int(cycle)

    def _trigger_next_vloops(self, inst, cycle: int):
        """
        Dynamically trigger:
        1) next sibling top-level block
        2) next nested block inside current top-level block
        """
        top_block_id = int(inst.top_block_id)
        depth = self._get_block_depth(top_block_id)
        bounds = self._get_block_bounds(top_block_id)
        if bool(inst.is_last_in_top_block):
            next_tbid = self._next_nonempty_top_block(top_block_id + 1)
            if next_tbid is not None:
                if next_tbid not in self.top_block_vloop_start:
                    self._set_top_block_vloop(next_tbid, cycle)
                    self._init_top_block_nested_starts(next_tbid, cycle)
        if depth <= 0:
            return
        end_levels = inst.block_end_levels
        if not isinstance(end_levels, list) or not end_levels:
            return
        iter_stack = inst.iter_stack
        if not isinstance(iter_stack, list):
            return
        if depth == 1:
            return
        if depth == 2:
            if 1 in end_levels and len(iter_stack) >= 1:
                i = int(iter_stack[0])
                I = int(bounds[0])
                cur_key = self._make_key(top_block_id, 'loop1', (i,))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))
                if i + 1 < I:
                    prev_start = self.vloop_start.get(cur_key, end_cy)
                    next_start = max(int(end_cy), int(prev_start) + self.loop1_min_feedback_gap)
                    self._set_vloop_start(self._make_key(top_block_id, 'loop1', (i + 1,)), next_start)
            return
        if depth == 3:
            if 2 in end_levels and len(iter_stack) >= 2:
                i = int(iter_stack[0])
                j = int(iter_stack[1])
                M = int(bounds[1])
                cur_key = self._make_key(top_block_id, 'loop2', (i, j))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))
                if j + 1 < M:
                    self._set_vloop_start(self._make_key(top_block_id, 'loop2', (i, j + 1)), end_cy)
            if 1 in end_levels and len(iter_stack) >= 1:
                i = int(iter_stack[0])
                K = int(bounds[0])
                M = int(bounds[1])
                cur_key = self._make_key(top_block_id, 'loop1', (i,))
                end_cy = self.last_dispatch_time.get(cur_key, int(cycle))
                if i + 1 < K:
                    prev_start = self.vloop_start.get(cur_key, end_cy)
                    next_loop1_start = max(int(end_cy), int(prev_start) + self.loop1_min_feedback_gap)
                    self._set_vloop_start(self._make_key(top_block_id, 'loop1', (i + 1,)), next_loop1_start)
                    if M > 0:
                        self._set_vloop_start(
                            self._make_key(top_block_id, 'loop2', (i + 1, 0)),
                            next_loop1_start + self.nested_vloop_initial_start_gap
                        )
            return

    def can_dispatch(self, inst, cycle):
        top_open = self.top_block_body_open_time.get(inst.top_block_id)
        if top_open is None or cycle < top_open:
            return False
        inner_key = self._current_inner_block_key(inst)
        if self.theoretical_limit_vloop_only or inner_key is None:
            return True
        open_cycle = self.body_open_time.get(inner_key)
        if open_cycle is None or cycle < open_cycle:
            return False
        if self.theoretical_limit_mode:
            return True
        iteration = inst.iter_stack[-1] if inst.iter_stack else 0
        if iteration == 0 and inner_key not in self.block_base_cycle:
            self.block_base_cycle[inner_key] = cycle
        base_cycle = self.block_base_cycle.get(inner_key)
        return base_cycle is not None and cycle >= base_cycle + int(iteration) * self.innermost_iter_dispatch_stride

    def notify_dispatch(self, inst, cycle):
        self._update_last_dispatch(inst, cycle)
        self._trigger_next_vloops(inst, cycle)
