"""Independent scalar encodings used by A5 low-precision conversion probes.

HiF8 fields follow the installed CANN kernel_hif8.h coding table. FP8/FP4
encodings are decoded numerically before selecting a representable result.
ROUND_H is accepted only for exactly representable inputs; stochastic/hybrid
edge behavior requires a separate reference.
"""

from functools import lru_cache
import math

PACKED = {"s4x2", "f4e1m2x2", "f4e2m1x2"}
FLOATING = {"hif8", "f8e4m3", "f8e5m2", "f4e1m2x2", "f4e2m1x2"}
TYPES = PACKED | FLOATING


def bits(dtype):
    return 4 if dtype in PACKED else 8


def value(code, dtype):
    if dtype == "s4x2":
        return code - 16 if code & 8 else code
    sign = -1 if code & (1 << (bits(dtype) - 1)) else 1
    n = code & ((1 << (bits(dtype) - 1)) - 1)
    if dtype == "hif8":
        if not n:
            return 0.0 if sign == 1 else math.nan
        if n < 8:
            return sign * 2.0 ** (n - 23)
        if n < 16:
            return sign * (1 + (n & 7) / 8)
        if n < 32:
            e, m = 1 if not n & 8 else -1, (n & 7) / 8
        elif n < 64:
            e, m = (2 + ((n >> 3) & 1)) * (-1 if n & 16 else 1), (n & 7) / 8
        elif n < 96:
            e, m = (4 + ((n >> 2) & 3)) * (-1 if n & 16 else 1), (n & 3) / 4
        else:
            e, m = (8 + ((n >> 1) & 7)) * (-1 if n & 16 else 1), (n & 1) / 2
        return sign * 2.0**e * (1 + m)
    exponent, mantissa, bias = {
        "f8e4m3": (4, 3, 7),
        "f8e5m2": (5, 2, 15),
        "f4e1m2x2": (1, 2, 1),
        "f4e2m1x2": (2, 1, 1),
    }[dtype]
    e, m = n >> mantissa, n & ((1 << mantissa) - 1)
    if dtype == "f8e4m3" and n == 0x7F:
        return math.nan
    if dtype == "f8e5m2" and e == 31:
        return sign * math.inf if not m else math.nan
    return sign * (
        2.0 ** (e - bias) * (1 + m / 2**mantissa)
        if e
        else 2.0 ** (1 - bias) * m / 2**mantissa
    )


@lru_cache(None)
def levels(dtype):
    return tuple(
        (code, value(code, dtype))
        for code in range(1 << bits(dtype))
        if math.isfinite(value(code, dtype))
    )


def code(value_, dtype, rounding="ROUND_R"):
    if dtype == "s4x2":
        return int(value_) & 15
    candidates = levels(dtype)
    exact = [c for c, x in candidates if x == value_]
    if exact:
        return min(exact)
    if rounding == "ROUND_H":
        raise ValueError("ROUND_H reference requires an exactly representable input")
    if rounding in ("ROUND_F", "ROUND_C", "ROUND_Z"):
        toward = (
            "ROUND_F"
            if rounding == "ROUND_Z" and value_ >= 0
            else "ROUND_C"
            if rounding == "ROUND_Z"
            else rounding
        )
        selection = (
            [(c, x) for c, x in candidates if x <= value_]
            if toward == "ROUND_F"
            else [(c, x) for c, x in candidates if x >= value_]
        )
        if selection:
            return (max if toward == "ROUND_F" else min)(
                selection, key=lambda item: item[1]
            )[0]
    return min(
        candidates,
        key=lambda item: (
            abs(item[1] - value_),
            -abs(item[1]) if rounding == "ROUND_A" else item[0] & 1,
            item[0],
        ),
    )[0]


def encode(values, dtype, rounding="ROUND_R"):
    codes = [code(x, dtype, rounding) for x in values]
    if dtype in PACKED:
        if len(codes) % 2:
            raise ValueError(
                "Packed conversion data must contain an even number of lanes"
            )
        return bytes(a | (b << 4) for a, b in zip(codes[::2], codes[1::2]))
    return bytes(codes)


def decode(raw, dtype):
    codes = [x for b in raw for x in (b & 15, b >> 4)] if dtype in PACKED else raw
    return [value(c, dtype) for c in codes]
