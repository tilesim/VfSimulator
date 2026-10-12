"""Resolve parameter files and decode configuration, with explicit format errors."""
from __future__ import annotations
import json
import os
from collections.abc import Mapping
from pathlib import Path
from copy import deepcopy
from .types import LoadedParameters, VfTiming
from .uarch import parse_uarch


def resolve_path(base_dir, *, explicit, env_key, candidates, required=True):
    selected = explicit or os.environ.get(env_key)
    if selected:
        path = Path(selected).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{env_key}: explicit path not found: {path}")
        return path
    paths = [Path(base_dir) / name for name in candidates]
    for path in paths:
        if path.is_file():
            return path.resolve()
    if required:
        raise FileNotFoundError(f"Could not locate {env_key}. Tried: " + ", ".join(str(p) for p in paths))
    return None


def read_json(path):
    if path is None:
        return {}
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid parameter JSON in {path}: {error.msg} at line {error.lineno}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Parameter file {path} must contain an object")
    return value


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def validate_pair_table(table, label, minimum):
    if not isinstance(table, dict):
        raise ValueError(f"{label} must be an object")
    for key, value in table.items():
        if isinstance(value, dict):
            validate_pair_table(value, f"{label}.{key}", minimum)
        else:
            _integer(value, f"{label}.{key}", minimum)


def merge_parameters(base: Mapping, overrides: Mapping) -> dict:
    result = deepcopy(dict(base))
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merge_parameters(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_parameters(base_dir, *, isa_path=None, uarch_path=None, forwarding_path=None, ii_path=None):
    isa = read_json(resolve_path(base_dir, explicit=isa_path, env_key="ISA_JSON_PATH",
                                candidates=("configs/isa.json", "isa.json")))
    uarch = read_json(resolve_path(base_dir, explicit=uarch_path, env_key="UARCH_JSON_PATH",
                                  candidates=("configs/uarch.json", "uarch.json")))
    forwarding = read_json(resolve_path(base_dir, explicit=forwarding_path, env_key="FORWARDING_JSON_PATH",
                                        candidates=("configs/forwarding.json", "forwarding.json"), required=False))
    ii = read_json(resolve_path(base_dir, explicit=ii_path, env_key="II_JSON_PATH",
                               candidates=("configs/InitiationInterval.json", "Initiation_Interval.json"), required=False))
    base_uarch = parse_uarch(uarch)
    defaults = isa.get("defaults", {})
    instructions = isa.get("instructions", {})
    if not isinstance(defaults, dict) or not isinstance(instructions, dict):
        raise ValueError("ISA defaults and instructions must be objects")
    version = isa.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported ISA schema_version")
    for op, node in instructions.items():
        if not isinstance(node, dict):
            raise ValueError(f"ISA instruction {op} must be an object")
        if version == 2:
            forms = node.get("forms", {})
            if not isinstance(forms, dict):
                raise ValueError(f"ISA {op}.forms must be an object")
            for form, params in forms.items():
                if not isinstance(params, dict):
                    raise ValueError(f"ISA {op}.{form} must be an object")
                if "latency" in params:
                    _integer(params["latency"], f"ISA {op}.{form}.latency")
    _integer(forwarding.get("default", forwarding.get("defaults", 3)), "forwarding.default")
    _integer(ii.get("defaults", 1), "II.defaults", 1)
    validate_pair_table(forwarding.get("forwarding", {}), "forwarding", 0)
    validate_pair_table(ii.get("InitiationInterval", {}), "II", 1)
    timing = VfTiming(_integer(defaults.get("vf_startup_cost", 0), "vf_startup_cost"),
                      _integer(defaults.get("vf_drain_cost", 0), "vf_drain_cost"))
    return LoadedParameters(isa, uarch, base_uarch, forwarding, ii, timing)
