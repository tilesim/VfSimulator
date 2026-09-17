#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List

from core.value_storage import ValueStorageLookup


class ProgramAnalyzer:
    def __init__(self, params: Dict[str, Any], values: Dict[str, Any] | None = None) -> None:
        self.params = params
        self.value_storage = ValueStorageLookup(values)

    def is_vreg_name(self, name: Any) -> bool:
        return self.value_storage.is_register(name)

    def resolve_bound(self, bound: Any) -> int:
        if isinstance(bound, int):
            return bound
        if isinstance(bound, str):
            if bound.isdigit():
                return int(bound)
            if bound in self.params:
                return int(self.params[bound])
        raise ValueError(f"Unsupported loop bound: {bound}")

    def resolve_unroll_value(self, unroll: Any) -> int:
        if isinstance(unroll, int):
            return max(1, int(unroll))
        if isinstance(unroll, str):
            if unroll.isdigit():
                return max(1, int(unroll))
            if unroll in self.params:
                return max(1, int(self.params[unroll]))
        return 1

    def iter_insts(self, node: Any):
        if isinstance(node, list):
            for x in node:
                yield from self.iter_insts(x)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "inst":
            yield node
        body = node.get("body")
        if isinstance(body, list):
            for x in body:
                yield from self.iter_insts(x)


    def infer_nested_bounds_from_loop(self, loop_node: Dict[str, Any]) -> List[int]:
        bounds: List[int] = []
        node = loop_node

        while isinstance(node, dict) and node.get("type") == "loop" and len(bounds) < 3:
            bounds.append(self.resolve_bound(node.get("iters")))
            body = node.get("body", [])

            next_loop = None
            if isinstance(body, list):
                for item in body:
                    if isinstance(item, dict) and item.get("type") == "loop":
                        next_loop = item
                        break

            if next_loop is None:
                break
            node = next_loop

        return bounds

    def infer_top_block_loop_bounds(
        self,
        program: List[Dict[str, Any]],
    ) -> Dict[int, List[int]]:
        result: Dict[int, List[int]] = {}
        tbid = 0

        if not isinstance(program, list):
            return {0: []}

        for node in program:
            if isinstance(node, dict) and node.get("type") == "loop":
                result[tbid] = self.infer_nested_bounds_from_loop(node)
                tbid += 1

        if not result:
            result[0] = []

        return result
