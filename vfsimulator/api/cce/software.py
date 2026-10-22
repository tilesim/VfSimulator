"""Instantiate the CANN 3101 software instruction recipes as adapter IR."""

from __future__ import annotations

from functools import cache
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .ir import AdapterAlias, AdapterInstruction, AdapterValue


@cache
def recipes_by_api():
    document = json.loads(
        (Path(__file__).resolve().parents[2] / "configs/cce_lowering.json").read_text()
    )
    grouped = {}
    for recipe in document["recipes"]:
        grouped.setdefault(recipe["api"], []).append(recipe)
    return grouped


def select_recipe(api: str, arguments: Sequence[str], dtypes: Sequence[str | None]):
    """Match public argument types and constant options before emitting nodes."""
    for recipe in recipes_by_api().get(api, ()):
        if len(arguments) > len(recipe["arguments"]):
            continue
        matched = True
        for operand in recipe["arguments"]:
            position = operand["position"]
            if operand["config"]:
                actual = (
                    arguments[position].strip()
                    if position < len(arguments)
                    else operand["default"]
                )
                expected = recipe["options"][str(position)]
                actual = actual.removeprefix("__cce_simd::") if actual else None
                if (
                    actual == "MODE_UNKNOWN"
                    and expected == "MODE_ZEROING"
                    and operand["default"] == "MODE_UNKNOWN"
                ):
                    actual = "MODE_ZEROING"
                matched &= actual == expected
            else:
                if position >= len(arguments):
                    matched = False
                    break
                declared = operand["type"].replace("&", "").strip()
                if declared.startswith("vector_"):
                    expected = declared.removeprefix("vector_")
                    from vfsimulator.api.common.symbols import normalize_dtype

                    matched &= dtypes[position] == normalize_dtype(expected)
                else:
                    matched &= dtypes[position] not in {"bool"} and not str(
                        dtypes[position]
                    ).startswith("2xvl_")
        if matched:
            return recipe
    return None


def emit_recipe(
    recipe,
    arguments: Mapping[int, AdapterValue | tuple[AdapterValue, AdapterValue]],
    temporary: Callable[[str], AdapterValue],
    scalar: Callable[[str], AdapterValue],
    location,
):
    """Preserve caller inputs, then bind SSA recipe results to caller outputs."""
    nodes = []
    captured = {}

    def argument(ref, snapshot=True):
        key = (ref["argument"], ref.get("part"))
        original = arguments[key[0]]
        value = original[key[1]] if key[1] is not None else original
        if snapshot and value.storage in {"Register", "PredicateRegister"}:
            if key not in captured:
                captured[key] = temporary(value.dtype)
                nodes.append(AdapterAlias(captured[key], value, location))
            return captured[key]
        return value

    def capture(ref):
        if isinstance(ref, dict):
            if "argument" in ref:
                argument(ref)
            for child in ref.get("operands", ()):
                capture(child)

    for step in recipe["steps"]:
        for ref in step["inputs"]:
            capture(ref)
    for assignment in recipe["assignments"]:
        capture(assignment["source"])
    bindings = {}
    destinations = {}
    for assignment in recipe["assignments"]:
        source = assignment["source"]
        if isinstance(source, str) and source not in bindings:
            bindings[source] = argument(assignment["destination"], False)
            destinations[source] = assignment["destination"]
    for identity, description in recipe["temporaries"].items():
        if identity not in bindings:
            bindings[identity] = temporary(description["dtype"])

    def resolve(ref):
        if isinstance(ref, str):
            return bindings[ref]
        if "argument" in ref:
            return argument(ref)
        # Scalar calculations in CANN software wrappers are outside VFSim's
        # scalar execution model. Their values retain distinct input identities.
        value = scalar(ref["dtype"])
        return value

    for step in recipe["steps"]:
        nodes.append(
            AdapterInstruction(
                step["opcode"],
                [resolve(ref) for ref in step["inputs"]],
                [bindings[identity] for identity in step["outputs"]],
                form=step["form"],
                instruction_class="compute",
                source_location=location,
                attributes={
                    "cann_intrinsic": step["intrinsic"],
                    **step.get("attributes", {}),
                },
            )
        )
    for assignment in recipe["assignments"]:
        source = assignment["source"]
        if (
            isinstance(source, str)
            and destinations.get(source) == assignment["destination"]
        ):
            continue
        target = argument(assignment["destination"], False)
        value = resolve(source)
        nodes.append(AdapterAlias(target, value, location, allow_bitcast=True))
    return nodes
