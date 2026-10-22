from __future__ import annotations

from enum import Enum
from typing import Any

from vfsimulator.api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from vfsimulator.api.common.schema import StorageKind


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class DType(_StrEnum):
    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"
    INT16 = "int16"
    INT64 = "int64"
    UINT64 = "uint64"
    FP8_E4M3 = "f8e4m3"
    FP8_E5M2 = "f8e5m2"
    FP8_E8M0 = "f8e8m0"
    HIF8 = "hif8"
    FP4_E1M2X2 = "f4e1m2x2"
    FP4_E2M1X2 = "f4e2m1x2"
    INT4X2 = "s4x2"
    INT32 = "int32"
    UINT32 = "uint32"
    UINT16 = "uint16"
    INT8 = "int8"
    UINT8 = "uint8"
    BOOL = "bool"


class MembarType(_StrEnum):
    VST_VLD = "VST_VLD"
    VLD_VST = "VLD_VST"
    VST_VST = "VST_VST"
    VV_ALL = "VV_ALL"


OpCode = _StrEnum(
    "OpCode",
    {opcode: opcode for opcode in DEFAULT_INSTRUCTION_CATALOG.specs},
    module=__name__,
)


DTYPE_ALIASES = {
    "s16": DType.INT16,
    "i16": DType.INT16,
    "int16": DType.INT16,
    "s64": DType.INT64,
    "i64": DType.INT64,
    "int64": DType.INT64,
    "u64": DType.UINT64,
    "uint64": DType.UINT64,
    "f32": DType.FP32,
    "float32": DType.FP32,
    "fp32": DType.FP32,
    "f16": DType.FP16,
    "float16": DType.FP16,
    "fp16": DType.FP16,
    "bf16": DType.BF16,
    "bfloat16": DType.BF16,
    "s32": DType.INT32,
    "i32": DType.INT32,
    "int32": DType.INT32,
    "u32": DType.UINT32,
    "uint32": DType.UINT32,
    "u16": DType.UINT16,
    "uint16": DType.UINT16,
    "s8": DType.INT8,
    "int8": DType.INT8,
    "u8": DType.UINT8,
    "uint8": DType.UINT8,
    "bool": DType.BOOL,
    "boolean": DType.BOOL,
    "predicate": DType.BOOL,
}

FORM_DTYPE_ALIASES = {
    "s16": DType.INT16,
    "i16": DType.INT16,
    "s64": DType.INT64,
    "u64": DType.UINT64,
    "u16": DType.UINT16,
    "f32": DType.FP32,
    "f16": DType.FP16,
    "bf16": DType.BF16,
    "s32": DType.INT32,
    "i32": DType.INT32,
    "u32": DType.UINT32,
    "s8": DType.INT8,
    "u8": DType.UINT8,
}

DTYPE_TO_COMPACT = {
    DType.INT16.value: "s16",
    DType.INT64.value: "s64",
    DType.UINT64.value: "u64",
    DType.UINT16.value: "u16",
    DType.FP32.value: "f32",
    DType.FP16.value: "f16",
    DType.BF16.value: "bf16",
    DType.INT32.value: "s32",
    DType.UINT32.value: "u32",
    DType.INT8.value: "s8",
    DType.UINT8.value: "u8",
    DType.BOOL.value: "bool",
}

DTYPE_ELEMENT_BYTES = {
    "fp32": 4,
    "fp16": 2,
    "bf16": 2,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "int64": 8,
    "uint64": 8,
    "f8e4m3": 1,
    "f8e5m2": 1,
    "f8e8m0": 1,
    "hif8": 1,
    "f4e1m2x2": 1,
    "f4e2m1x2": 1,
    "s4x2": 1,
}


def dtype_element_bytes(dtype: str | None) -> int | None:
    """Pointer element width; packed x2 types contain two values per byte."""
    return DTYPE_ELEMENT_BYTES.get(normalize_dtype(dtype))


STORAGE_ALIASES = {
    "predicateregister": StorageKind.PREDICATE_REGISTER,
    "register": StorageKind.REGISTER,
    "reg": StorageKind.REGISTER,
    "vreg": StorageKind.REGISTER,
    "ub": StorageKind.UB,
    "unified_buffer": StorageKind.UB,
    "memory": StorageKind.UB,
    "mem": StorageKind.UB,
    "scalar": StorageKind.SCALAR,
    "imm": StorageKind.SCALAR,
}

MEMBAR_ALIASES = {
    "vst_vld": MembarType.VST_VLD,
    "vld_vst": MembarType.VLD_VST,
    "vst_vst": MembarType.VST_VST,
    "vv_all": MembarType.VV_ALL,
}


def _symbol_text(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def normalize_dtype(value: Any | None, *, default: str | None = None) -> str | None:
    if value is None or value == "":
        return default
    text = _symbol_text(value).strip().lower()
    normalized = DTYPE_ALIASES.get(text)
    return normalized.value if normalized is not None else text


def normalize_form(value: Any | None, *, default: str | None = None) -> str | None:
    if value is None or value == "":
        return default
    text = _symbol_text(value).strip().lower()
    if "_to_" in text:
        src, dst = text.split("_to_", 1)
        src_dtype = FORM_DTYPE_ALIASES.get(src, DTYPE_ALIASES.get(src))
        dst_dtype = FORM_DTYPE_ALIASES.get(dst, DTYPE_ALIASES.get(dst))
        src_key = DTYPE_TO_COMPACT.get(src_dtype.value if src_dtype else src, src)
        dst_key = DTYPE_TO_COMPACT.get(dst_dtype.value if dst_dtype else dst, dst)
        return f"{src_key}_to_{dst_key}"
    return normalize_dtype(text, default=default)


def compact_dtype(value: Any | None) -> str | None:
    dtype = normalize_dtype(value)
    if dtype is None:
        return None
    return DTYPE_TO_COMPACT.get(dtype, dtype)


def normalize_opcode(value: Any) -> str:
    return DEFAULT_INSTRUCTION_CATALOG.canonical_opcode(_symbol_text(value))


def normalize_storage(value: Any) -> str:
    text = _symbol_text(value).strip()
    normalized = STORAGE_ALIASES.get(text.lower())
    if normalized is None:
        raise ValueError(f"Unsupported value storage: {value}")
    return normalized.value


def normalize_membar_type(value: Any | None, *, default: str = "VST_VLD") -> str:
    text = _symbol_text(value if value not in (None, "") else default).strip().upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    normalized = MEMBAR_ALIASES.get(text.lower())
    return normalized.value if normalized is not None else text


def specialize_opcode(op: Any, form: Any | None) -> str:
    canonical_op = normalize_opcode(op)
    canonical_form = normalize_form(form) if form else None
    return DEFAULT_INSTRUCTION_CATALOG.specialize(canonical_op, canonical_form)


__all__ = [
    "DType",
    "DTYPE_ALIASES",
    "MEMBAR_ALIASES",
    "MembarType",
    "OpCode",
    "STORAGE_ALIASES",
    "StorageKind",
    "compact_dtype",
    "normalize_dtype",
    "normalize_form",
    "normalize_membar_type",
    "normalize_opcode",
    "normalize_storage",
    "specialize_opcode",
]
