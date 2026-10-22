"""CCE input boundary: parse, version definitions, and validate CanonicalVfInfo."""
from pathlib import Path
from typing import Mapping

from vfsimulator.api.cce.source import extract_cce_vf_scopes, list_cce_vf_kernels
from vfsimulator.api.cce.parser import _parse_cce_adapter_program
from vfsimulator.api.common.schema import CanonicalVfInfo
from vfsimulator.api.common.validator import require_valid_canonical_vf_info
from vfsimulator.api.cce.value_versioning import ValueVersioningPass


def parse_cce_canonical_vf_info(
    path: str | Path,
    kernel_name: str | None = None,
    loop_params: Mapping[str, int] | None = None,
) -> CanonicalVfInfo:
    program = _parse_cce_adapter_program(path, kernel_name, loop_params)
    vf_info = ValueVersioningPass().run(
        program, source={"adapter": "cce", "path": str(Path(path))}
    )
    return require_valid_canonical_vf_info(vf_info)


__all__ = ["parse_cce_canonical_vf_info", "list_cce_vf_kernels", "extract_cce_vf_scopes"]
