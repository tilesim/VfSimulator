"""CCE source scanning, VF scope extraction, and token helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

from api.common.symbols import normalize_dtype


_FUNC_RE = re.compile(
    r"\bvoid\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>.*?)\)\s*\{",
    re.DOTALL,
)


@dataclass(frozen=True)
class CCEVFScope:
    kernel_name: str
    start_line: int
    end_line: int
    source: str
    declaration_source: str
    params: Sequence[str]
    param_storage: Dict[str, str]
    param_dtypes: Dict[str, str]
    source_path: str


def list_cce_vf_kernels(path: str | Path) -> List[str]:
    """Return all function names that contain a ``__VEC_SCOPE__`` block."""

    return [scope.kernel_name for scope in extract_cce_vf_scopes(path)]


def extract_cce_vf_scopes(path: str | Path) -> List[CCEVFScope]:
    """Extract every VF scope, preserving its file and line identity."""
    source_path = Path(path)
    return _extract_cce_vf_scopes(
        _strip_comments(source_path.read_text(encoding="utf-8")), source_path
    )


def _extract_cce_vf_scopes(clean: str, source_path: Path) -> List[CCEVFScope]:
    scopes: List[CCEVFScope] = []
    for match in _FUNC_RE.finditer(clean):
        name = match.group("name")
        open_brace = match.end() - 1
        close_brace = _find_matching_brace(clean, open_brace)
        body = clean[open_brace + 1 : close_brace]

        vec_match = re.search(r"__VEC_SCOPE__\s*\{", body)
        if not vec_match:
            continue

        vec_open = open_brace + 1 + vec_match.end() - 1
        vec_close = _find_matching_brace(clean, vec_open)
        vec_source = clean[vec_open + 1 : vec_close]
        scopes.append(
            CCEVFScope(
                kernel_name=name,
                start_line=_line_number(clean, vec_open),
                end_line=_line_number(clean, vec_close),
                source=vec_source,
                declaration_source=body[: vec_match.start()],
                params=_parse_param_names(match.group("params")),
                param_storage=_parse_param_storage(match.group("params")),
                param_dtypes=_parse_param_dtypes(match.group("params")),
                source_path=str(source_path),
            )
        )

    return scopes


def _select_scope(scopes: Sequence[CCEVFScope], kernel_name: str | None) -> CCEVFScope:
    if not scopes:
        raise ValueError("No __VEC_SCOPE__ kernel found in CCE file")
    if kernel_name is None:
        if len(scopes) == 1:
            return scopes[0]
        names = ", ".join(scope.kernel_name for scope in scopes)
        raise ValueError(f"Multiple __VEC_SCOPE__ kernels found; select one of: {names}")

    matches = [scope for scope in scopes if scope.kernel_name == kernel_name]
    if not matches:
        names = ", ".join(scope.kernel_name for scope in scopes)
        raise ValueError(f"Kernel '{kernel_name}' not found. Available kernels: {names}")
    return matches[0]


def _parse_param_names(params: str) -> List[str]:
    return list(_parse_param_storage(params))


def _parse_param_storage(params: str) -> Dict[str, str]:
    storage: Dict[str, str] = {}
    for raw in _split_args(params):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.search(r"([A-Za-z_]\w*)\s*(?:=[^,]+)?$", cleaned)
        if not match:
            continue
        name = match.group(1)
        storage[name] = "UB" if "__ubuf__" in cleaned else "Scalar"
    return storage


def _parse_param_dtypes(params: str) -> Dict[str, str]:
    dtypes: Dict[str, str] = {}
    for raw in _split_args(params):
        cleaned = raw.strip()
        match = re.search(
            r"__ubuf__\s+([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)",
            cleaned,
        )
        if match is None:
            match = re.fullmatch(
                r"(?:(?:const|volatile)\s+)*"
                r"(u?int(?:8|16|32|64)_t|size_t|unsigned|signed|short|int|"
                r"long|float|double|half)\s+([A-Za-z_]\w*)"
                r"(?:\s*=\s*.+)?",
                cleaned,
            )
        if match:
            dtypes[match.group(2)] = _cce_scalar_dtype_to_form(match.group(1))
    return dtypes


def _cce_scalar_dtype_to_form(dtype: str) -> str:
    normalized = dtype.lower()
    aliases = {
        "int": "int32",
        "unsigned": "uint32",
        "unsigned int": "uint32",
        "float": "fp32",
        "half": "fp16",
        "bfloat16": "bf16",
        "bfloat16_t": "bf16",
    }
    if re.fullmatch(r"u?int(?:8|16|32|64)_t", normalized):
        normalized = normalized[:-2]
    return aliases.get(
        normalized,
        str(normalize_dtype(normalized, default=normalized)),
    )


def _vector_dtype_to_form(dtype: str) -> str:
    return str(normalize_dtype(dtype, default=str(dtype).lower()))


def _split_args(text: str) -> List[str]:
    args: List[str] = []
    start = 0
    depth = 0
    for idx, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[start:idx].strip())
            start = idx + 1
    tail = text[start:].strip()
    if tail:
        args.append(tail)
    return args


def _matching_close_parenthesis(text: str, start: int = 0) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                return None
    return None


def _strip_ub_reference_wrappers(expression: str) -> str:
    """Strip complete UB pointer casts and grouping around an address expression."""

    value = expression.strip()
    while value.startswith("("):
        close = _matching_close_parenthesis(value)
        if close is None:
            break
        if close == len(value) - 1:
            value = value[1:close].strip()
            continue
        cast = value[1:close]
        if "__ubuf__" in cast and ("*" in cast or "&" in cast):
            value = value[close + 1 :].strip()
            continue
        break
    return value


def _base_identifier(arg: str) -> str:
    text = arg.strip()
    identifiers = re.findall(r"[A-Za-z_]\w*", text)
    type_tokens = {
        "__ubuf__",
        "bool",
        "const",
        "float",
        "half",
        "int",
        "int16_t",
        "int32_t",
        "int64_t",
        "int8_t",
        "long",
        "short",
        "signed",
        "uint16_t",
        "uint32_t",
        "uint64_t",
        "uint8_t",
        "unsigned",
        "vector_bool",
        "vector_f16",
        "vector_f32",
        "vector_s16",
        "vector_s32",
        "vector_u16",
        "vector_u32",
        "volatile",
    }
    for name in reversed(identifiers):
        if name not in type_tokens:
            return name
    return identifiers[-1] if identifiers else text


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.DOTALL)
    source = re.sub(r"//.*", "", source)
    return source


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _is_token_boundary(text: str, pos: int) -> bool:
    return pos >= len(text) or not (text[pos].isalnum() or text[pos] == "_")


def _find_matching_brace(text: str, open_pos: int) -> int:
    return _find_matching_delim(text, open_pos, "{", "}")


def _find_matching_paren(text: str, open_pos: int) -> int:
    return _find_matching_delim(text, open_pos, "(", ")")


def _find_matching_delim(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    if open_pos < 0 or open_pos >= len(text) or text[open_pos] != open_ch:
        raise ValueError(f"Expected '{open_ch}' at position {open_pos}")
    depth = 0
    for idx in range(open_pos, len(text)):
        ch = text[idx]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    raise ValueError(f"Unmatched '{open_ch}' at position {open_pos}")


def _line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1
