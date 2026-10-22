from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping

from vfsimulator.api.cce.adapter import parse_cce_canonical_vf_info
from vfsimulator.api.programmatic.builder import VfInfoBuilder
from vfsimulator.api.common.diagnostics import ValidationResult
from vfsimulator.api.common.schema import CanonicalVfInfo, ScalarValue
from vfsimulator.api.common.validator import (
    require_valid_canonical_vf_info,
    validate_canonical_vf_info,
)
from vfsimulator.api.json.adapter import CanonicalJsonVfInfoAdapter


class InputAPI:
    """
    Repository-level input boundary for simulator frontends.

    All formal loaders produce the versioned ``CanonicalVfInfo`` contract.
    """

    @staticmethod
    def load_json(path: str | Path) -> CanonicalVfInfo:
        """Load and validate a versioned CanonicalVfInfo JSON document."""

        return CanonicalJsonVfInfoAdapter.load(path)

    @staticmethod
    def load_cce(
        path: str | Path,
        kernel_name: str | None = None,
        loop_params: Dict[str, int] | None = None,
    ) -> CanonicalVfInfo:
        return parse_cce_canonical_vf_info(
            path,
            kernel_name=kernel_name,
            loop_params=loop_params,
        )

    @staticmethod
    def from_canonical(vf_info: CanonicalVfInfo) -> CanonicalVfInfo:
        """Validate a directly constructed object and return it for Core execution."""
        return require_valid_canonical_vf_info(vf_info)

    @staticmethod
    def validate_canonical_vf_info(vf_info: CanonicalVfInfo) -> ValidationResult:
        """Validate the versioned frontend contract without mutating it."""

        return validate_canonical_vf_info(vf_info)

    @staticmethod
    def new_builder(
        *,
        params: Mapping[str, int] | None = None,
        uarch: Mapping[str, ScalarValue] | None = None,
        source: Mapping[str, ScalarValue] | None = None,
    ) -> VfInfoBuilder:
        """Create an explicit builder for the versioned canonical contract."""

        return VfInfoBuilder(params=params, uarch=uarch, source=source)
