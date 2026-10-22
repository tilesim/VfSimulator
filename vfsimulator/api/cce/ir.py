from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from vfsimulator.api.common.schema import SourceLocation
from vfsimulator.api.common.symbols import (
    normalize_dtype,
    normalize_storage,
)


AdapterStorageKind: TypeAlias = Literal["Register", "PredicateRegister", "UB", "Scalar"]


@dataclass(frozen=True)
class AdapterValue:
    value_id: str
    storage: AdapterStorageKind = "Register"
    dtype: str | None = None
    shape: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.value_id:
            raise ValueError("AdapterValue requires a non-empty value_id")
        object.__setattr__(self, "storage", normalize_storage(self.storage))
        object.__setattr__(self, "dtype", normalize_dtype(self.dtype))
        object.__setattr__(self, "shape", tuple(int(dim) for dim in self.shape))


AdapterValueRef: TypeAlias = "str | AdapterValue"


@dataclass(frozen=True)
class AdapterMemoryAccess:
    value_id: str
    access_kind: Literal["read", "write"]
    offset: int | str = 0
    span: int | None = None
    mode: str | None = None
    address_state_id: str | None = None
    update_mode: str = "none"
    post_update_delta_bytes: int | str | None = None
    address_unit_bytes: int | None = None


@dataclass
class AdapterInstruction:
    name: str
    src: list[AdapterValueRef]
    dst: list[AdapterValueRef]
    form: str | None = None
    instruction_class: str | None = None
    memory_accesses: tuple[AdapterMemoryAccess, ...] = ()
    source_location: SourceLocation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterAlias:
    destination: AdapterValueRef
    source: AdapterValueRef
    source_location: SourceLocation | None = None
    allow_bitcast: bool = False


@dataclass
class AdapterMembar:
    type: str = "VST_VLD"
    source_location: SourceLocation | None = None


@dataclass
class AdapterLoop:
    count: int | str
    unroll: int | str = 1
    body: list[AdapterNode] = field(default_factory=list)
    loop_id: str | None = None
    induction_variable: str | None = None
    induction_start: int | str = 0
    induction_step: int | str = 1
    source_location: SourceLocation | None = None


AdapterNode: TypeAlias = (
    "AdapterLoop | AdapterInstruction | AdapterAlias | AdapterMembar"
)


@dataclass
class AdapterProgram:
    context: list[AdapterNode]
    values: dict[str, AdapterValue] = field(default_factory=dict)
    params: dict[str, int] = field(default_factory=dict)
    default_dtype: str = "fp32"
    uarch: dict[str, Any] = field(default_factory=dict)
    live_in_values: tuple[str, ...] = ()


__all__ = [
    "AdapterAlias",
    "AdapterInstruction",
    "AdapterLoop",
    "AdapterMemoryAccess",
    "AdapterMembar",
    "AdapterNode",
    "AdapterProgram",
    "AdapterValue",
]
