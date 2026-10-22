"""Predict typed TileSim programs with isolated runs and reusable parameters."""

from functools import lru_cache
import json
from pathlib import Path

from vfsimulator.api.json.serialization import canonical_vf_info_to_dict
from vfsimulator.core.params.loader import resolve_path
from vfsimulator.core.simulator import CoreVfCostModel
from .adapter import program_to_canonical
from .program import VfSimProgram


def _parameter_version(base_dir):
    """Track selected files so a new call observes configuration updates."""
    version = []
    for env_key, candidates, required in (
        ("ISA_JSON_PATH", ("configs/isa.json", "isa.json"), True),
        ("UARCH_JSON_PATH", ("configs/uarch.json", "uarch.json"), True),
        ("FORWARDING_JSON_PATH", ("configs/forwarding.json", "forwarding.json"), False),
        ("II_JSON_PATH", ("configs/InitiationInterval.json", "Initiation_Interval.json"), False),
    ):
        path = resolve_path(base_dir, explicit=None, env_key=env_key,
                            candidates=candidates, required=required)
        if path is None:
            version.append(None)
        else:
            stat = path.stat()
            version.append((str(path), stat.st_mtime_ns, stat.st_ctime_ns,
                            stat.st_size, stat.st_ino))
    return tuple(version)


@lru_cache(maxsize=8)
def _model(base_dir, out_dir, fallback_dtype, parameter_version):
    # A run creates its own ParamDB, warnings, Engine and hardware state.
    return CoreVfCostModel(base_dir=base_dir, out_dir=out_dir,
                           fallback_dtype=fallback_dtype)


def predict_from_program(
    program: VfSimProgram, *, config_root: str | Path | None = None,
    out_dir: str | Path | None = "results/program_api", model: str = "mainline",
    dump_trace_path: str | Path | None = None, dump_results: bool = True,
):
    """Return the TileSim result envelope after one API validation.

    ``dump_results=False`` disables simulation files independently of the
    explicitly requested Canonical JSON dump. Cached models reuse parameter
    snapshots; configuration path or file changes select a fresh snapshot.
    """
    if model != "mainline":
        raise ValueError(f"Only the mainline model is supported, got {model!r}")
    canonical = program_to_canonical(program)
    if dump_trace_path is not None:
        path = Path(dump_trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(canonical_vf_info_to_dict(canonical), indent=2),
                        encoding="utf-8")
    base_dir = Path(config_root) if config_root is not None else Path(__file__).resolve().parents[2]
    base_dir = base_dir.resolve()
    output = str(out_dir) if dump_results and out_dir is not None else None
    simulator = _model(str(base_dir), output, program.dtype, _parameter_version(base_dir))
    result = simulator.run_vf_info(canonical)
    return {
        "cycles": int(result["vf_end_cycle"]), "model": model, "raw": result,
        "trace_path": str(dump_trace_path) if dump_trace_path is not None else None,
    }
