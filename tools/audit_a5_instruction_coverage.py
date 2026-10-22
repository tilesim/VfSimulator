"""Inventory the installed A5 SIMD VF API and VFSim's explicit timing entries.

Preprocess with the target CCEC, so macro expansions and architecture guards
come from the compiler. This reports declarations and configuration coverage;
it does not infer measurements from a configured number or from a fallback.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.common.instruction_catalog import instruction_catalog_from_dict
from api.common.symbols import compact_dtype, normalize_dtype

HEADERS = (
    "__clang_cce_vector_intrinsics.h",
    "npu_arch_3101/__clang_cce_vector_intrinsics.h",
)
EXCLUDED = {
    "get_repeat_ceiling": "标量配置查询",
    "set_ub_addr_upper_bound": "UB 边界特殊寄存器配置",
    "sprclr": "特殊寄存器控制",
    "sprsti": "特殊寄存器控制",
    "sprsts": "特殊寄存器控制",
}
ALIASES = {
    **{f"plt_b{w}": "PLT" for w in (8, 16, 32)},
    **{f"pltm_b{w}": "PLTM" for w in (8, 16, 32)},
    "plt_2xvl_b64": "PLT",
    "pltm_2xvl_b64": "PLTM",
    "pset_2xvl_b64": "PSET_B32",
}
TARGET_REJECTIONS = {
    "vnop",
    "vsadd",
    "vsadds",
    "vssub",
    "pslide_b8",
    "pslide_b16",
    "pslide_b32",
}
CONVERSIONS = {"vcvt", "vfcvt", "vsfcvt", "vscvt", "vunpack", "vmulscvt", "vpack"}
COMPOSITE = {"vload", "vstore", "vmod"}
SCALAR_BARRIERS = {
    "VS_ALL",
    "VST_LD",
    "VLD_ST",
    "VST_ST",
    "SV_ALL",
    "ST_VLD",
    "LD_VST",
    "ST_VST",
}
FUNCTION = re.compile(
    r"\[aicore\]\s+(?:static|inline)\s+"
    r"__attribute__\(\(overloadable, cce_builtin_api, always_inline\)\)\s+"
    r"([\w:&<> ]+?)\s+(\w+)\s*\("
)
LITERALS = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S
)


def balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 1
    for i in range(start, len(text)):
        depth += (text[i] == opening) - (text[i] == closing)
        if depth == 0:
            return i
    raise ValueError(f"Unbalanced {opening} at {start}")


def declarations(preprocessed: str) -> list[dict]:
    constants_by_type = defaultdict(set)
    for constant_type, name in re.findall(
        r"constexpr\s+(\w+)\s+(\w+)\s*=", preprocessed
    ):
        if name.isupper():
            constants_by_type[constant_type].add(name)
    chunks, locations, offsets = [], [], []
    path, line, offset = "", 0, 0
    for text in preprocessed.splitlines():
        marker = re.match(r'# (\d+) "([^"]+)"', text)
        if marker:
            line, path = int(marker[1]), marker[2]
            continue
        if path.endswith("/__clang_cce_vector_intrinsics.h"):
            relative = ("npu_arch_3101/" if "/npu_arch_3101/" in path else "") + Path(
                path
            ).name
            chunks.append(text + "\n")
            offsets.append(offset)
            locations.append((relative, line))
            offset += len(text) + 1
        line += 1
    text = "".join(chunks)
    masked = LITERALS.sub(lambda m: " " * len(m[0]), text)
    result = {}
    for match in FUNCTION.finditer(masked):
        args_end = balanced_end(masked, match.end(), "(", ")")
        body_start = masked.find("{", args_end)
        body_end = balanced_end(masked, body_start + 1, "{", "}")
        args = " ".join(text[match.end() : args_end].split())
        body = text[body_start + 1 : body_end]
        name, returns = match[2], match[1].strip()
        header, line = locations[bisect_right(offsets, match.start()) - 1]
        key = (name, returns, args)
        entry = result.setdefault(
            key,
            {
                "api": name,
                "returns": returns,
                "arguments": args,
                "sources": [],
                "builtins": [],
                "options": [],
            },
        )
        entry["sources"].append({"header": header, "line": line})
        entry["builtins"] = sorted(
            set(entry["builtins"]) | set(re.findall(r"__builtin_cce_\w+", body))
        )
        options = set(entry["options"])
        for symbol in re.findall(r"__cce_simd::(\w+)", body + args):
            options.update(constants_by_type.get(symbol, ()))
            if symbol.isupper():
                options.add(symbol)
        entry["options"] = sorted(options)
    if not result:
        raise ValueError("No SIMD VF declarations found; check the CCEC target")
    return list(result.values())


def vector_types(entry: dict) -> list[str]:
    return re.findall(r"\bvector_(\w+)\b", entry["returns"] + " " + entry["arguments"])


def vector_argument_dtypes(entry: dict) -> list[dict]:
    return [
        {
            "name": name,
            "dtype": str(normalize_dtype(dtype)),
            "reference": bool(reference),
        }
        for dtype, reference, name in re.findall(
            r"\bvector_(\w+)\s*([&*]?)\s*(\w+)", entry["arguments"]
        )
        if dtype not in ("bool", "align", "address")
    ]


def variant_form(entry: dict) -> str:
    name = entry["api"]
    types = [t for t in vector_types(entry) if t not in ("bool", "align", "address")]
    if name in CONVERSIONS or name.startswith("vcvt_"):
        if len(types) >= 2:
            return f"{compact_dtype(types[1])}_to_{compact_dtype(types[0])}"
    if name in {"vcadd", "vexpdif"} and len(types) >= 2:
        # Integer 16-bit inputs accumulate into a 32-bit result. The source
        # selects the physical reduction encoding and its timing form.
        return str(normalize_dtype(types[1]))
    if name in {"plt_2xvl_b64", "pltm_2xvl_b64", "pset_2xvl_b64"}:
        return "b32"
    if name == "vgather2":
        pointer = re.search(
            r"(?:cce_unif_buff\)\)|__ubuf__)\s*(\w+)\s*\*", entry["arguments"]
        )
        if pointer:
            return {
                "half": "fp16",
                "float": "fp32",
                "bfloat16_t": "bf16",
                "int8_t": "int8",
                "uint8_t": "uint8",
                "float8_e4m3_t": "f8e4m3",
                "float8_e5m2_t": "f8e5m2",
                "float8_e8m0_t": "f8e8m0",
            }.get(pointer[1], str(normalize_dtype(pointer[1].removesuffix("_t"))))
    if types:
        return str(normalize_dtype(types[0]))
    suffix = re.search(r"_(b8|b16|b32|b64)$", name)
    if suffix:
        return suffix[1]
    # Alignment-only calls carry their data type through the UB pointer.
    pointer = re.search(
        r"\b(int\d+_t|uint\d+_t|float|half|bfloat16_t|float\w+_t)\s*\*",
        entry["arguments"],
    )
    if pointer and "align" in vector_types(entry):
        return {
            "float": "fp32",
            "half": "fp16",
            "__cce_half": "fp16",
            "bfloat16_t": "bf16",
            "float8_e8m0_t": "f8e8m0",
            "float8_e4m3_t": "f8e4m3",
            "float8_e5m2_t": "f8e5m2",
            "float4_e1m2x2_t": "f4e1m2x2",
            "float4_e2m1x2_t": "f4e2m1x2",
        }.get(pointer[1], str(normalize_dtype(pointer[1].removesuffix("_t"))))
    return "b8" if "bool" in vector_types(entry) else "control"


def is_composite(entry: dict) -> bool:
    return (
        entry["api"] in COMPOSITE
        or "2xvl_" in entry["arguments"]
        or (entry["api"] == "vdiv" and variant_form(entry) not in ("fp16", "fp32"))
    )


def matched_forms(name: str, forms: list[str], spec) -> list[str]:
    if spec is None or name in ALIASES:
        return []
    available = set(spec.forms) | set(spec.specializations)
    matched = set(forms) & available
    # These catalog entries use an explicit bit-width form instead of dtype.
    # Restrict the correspondence to that width; a fixed form alone does not
    # prove that every CANN overload is represented by the model.
    if name == "vor" and spec.fixed_form == "b32":
        matched |= set(forms) & {"fp32", "int32", "uint32"}
    elif name == "vsstb" and spec.fixed_form == "b16":
        matched |= set(forms) & {"fp16", "bf16", "s16", "uint16"}
    elif name == "vpack" and spec.fixed_form == "b32":
        matched |= set(forms) & {"u32_to_u16", "s32_to_u16"}
    return sorted(matched)


def analyze(entries: list[dict], root: Path, signature_report=None) -> dict:
    contracts = {
        (r["api"], r["arguments"]): r
        for r in (signature_report or {}).get("records", [])
    }
    config_paths = [
        root / "configs" / n
        for n in (
            "instruction_catalog.json",
            "isa.json",
            "forwarding.json",
            "InitiationInterval.json",
        )
    ]
    raw_catalog, isa, forwarding, ii = [json.loads(p.read_text()) for p in config_paths]
    catalog = instruction_catalog_from_dict(raw_catalog)
    groups = defaultdict(list)
    for entry in entries:
        entry["vector_argument_dtypes"] = vector_argument_dtypes(entry)
        groups[entry["api"]].append(entry)
    rows = []
    for name, variants in sorted(groups.items()):
        if name in EXCLUDED:
            continue
        spec = catalog.lookup(ALIASES.get(name, name))
        forms = sorted({variant_form(v) for v in variants})
        # PLT uses a synthetic adapter spelling; the official plt_b* return-value
        # spellings need parser lowering before catalog presence means support.
        matched = matched_forms(name, forms, spec)
        if contracts:
            matched = sorted(
                {
                    variant_form(v)
                    for v in variants
                    if contracts.get((name, v["arguments"]), {}).get("passed")
                }
            )
        missing = sorted(set(forms) - set(matched))
        excluded_forms = []
        if name == "mem_bar":
            all_modes = {o for v in variants for o in v["options"]}
            excluded_forms = sorted(all_modes & SCALAR_BARRIERS)
            forms = sorted(all_modes - SCALAR_BARRIERS)
            matched = sorted(set(forms) & {"VST_VLD", "VLD_VST", "VST_VST", "VV_ALL"})
            missing = sorted(set(forms) - set(matched))
        rejected = name in TARGET_REJECTIONS
        if rejected:
            missing = []
        rows.append(
            {
                "api": name,
                "opcode": spec.opcode if spec else None,
                "category": (
                    "control"
                    if name in ("mem_bar", "vnop")
                    else "memory"
                    if any("cce_unif_buff" in v["arguments"] for v in variants)
                    else "address"
                    if any("address" in vector_types(v) for v in variants)
                    else "predicate"
                    if all(
                        t in ("bool", "align", "address")
                        for v in variants
                        for t in vector_types(v)
                    )
                    else "vector"
                ),
                "overloads": len(variants),
                "cann_forms": forms,
                "excluded_forms": excluded_forms,
                "catalog_forms_present": matched,
                "forms_missing": missing,
                "catalog_status": "target_rejected"
                if rejected
                else "full_forms"
                if matched and not missing
                else "partial_forms"
                if matched
                else "missing",
                "adapter_validated_overloads": sum(
                    contracts.get((name, v["arguments"]), {}).get("passed", False)
                    for v in variants
                ),
                "physical_lowering_forms": sorted(
                    {
                        f
                        for v in variants
                        for f in contracts.get((name, v["arguments"]), {}).get(
                            "physical_forms", []
                        )
                    }
                ),
                "requires_expansion": any(is_composite(v) for v in variants),
                "options": sorted({o for v in variants for o in v["options"]}),
                "mixed_dtype_signatures": sorted(
                    {
                        ", ".join(
                            f"{a['name']}={a['dtype']}"
                            for a in v["vector_argument_dtypes"]
                        )
                        for v in variants
                        if len({a["dtype"] for a in v["vector_argument_dtypes"]}) > 1
                    }
                ),
                "variants": variants,
            }
        )
    evidence_by_key = defaultdict(set)
    for path in (root / "skills/vfsim-instruction-benchmark/references/evidence").glob(
        "a5-*.json"
    ):
        document = json.loads(path.read_text())
        if not isinstance(document, dict):
            continue
        for key in document.get("parameters", {}).get("isa", {}):
            evidence_by_key[key].add(str(path.relative_to(root)))
    width_forms = {
        "PSET_B16",
        "PSET_B32",
        "PLT",
        "PLTM",
        "PINTLV",
        "PDINTLV",
        "PSLIDE",
        "MOVP_B16",
        "MOVP_B32",
        "VLDS_PAIR",
        "VSTS_BYTES",
        "VLDAS_BYTES",
        "VLDUS_BYTES",
        "VSTUS_BYTES",
        "VSTAS_BYTES",
    }
    form_rows = []
    for opcode, spec in catalog.specs.items():
        if spec.virtual:
            continue
        for form in sorted(spec.forms):
            key = f"{opcode}.{form}"
            timing = isa["instructions"].get(opcode, {}).get("forms", {}).get(form)
            fwd_out = forwarding["forwarding"].get(key, {})
            fwd_in = sum(
                key in consumers for consumers in forwarding["forwarding"].values()
            )
            ii_out = ii["InitiationInterval"].get(key, {})
            ii_in = sum(
                key in consumers for consumers in ii["InitiationInterval"].values()
            )
            form_rows.append(
                {
                    "key": key,
                    "class": spec.instruction_class.value,
                    "scope": "compatibility"
                    if form in {"b16", "b32"} and opcode not in width_forms
                    else "typed_or_physical",
                    "latency_evidence": sorted(evidence_by_key.get(key, ())),
                    "latency": timing.get("latency") if timing else None,
                    "exu": timing.get("EXU") if timing else None,
                    "dispatch_exu": timing.get("dispatch_exu") if timing else None,
                    "self_ii": ii_out.get(key),
                    "forwarding_exact_out": sum(k != "*" for k in fwd_out),
                    "forwarding_exact_in": fwd_in,
                    "forwarding_wildcard_out": fwd_out.get("*"),
                    "ii_exact_out": sum(k != "*" for k in ii_out),
                    "ii_exact_in": ii_in,
                }
            )
    stats = {
        "cann_api_names": len(rows),
        "cann_overloads": sum(r["overloads"] for r in rows),
        "cann_api_forms": sum(len(r["cann_forms"]) for r in rows),
        "catalog_name_matches": sum(
            r["opcode"] is not None or bool(r["physical_lowering_forms"]) for r in rows
        ),
        "adapter_validated_overloads": sum(
            r["adapter_validated_overloads"] for r in rows
        ),
        "target_rejected_apis": sum(
            r["catalog_status"] == "target_rejected" for r in rows
        ),
        "catalog_form_status": dict(Counter(r["catalog_status"] for r in rows)),
        "catalog_opcodes": len(catalog.specs),
        "catalog_physical_forms": len(form_rows),
        "latency_exact_forms": sum(r["latency"] is not None for r in form_rows),
        "latency_missing_forms": sum(r["latency"] is None for r in form_rows),
        "typed_physical_forms": sum(
            r["scope"] == "typed_or_physical" for r in form_rows
        ),
        "typed_latency_missing_forms": sum(
            r["scope"] == "typed_or_physical" and r["latency"] is None
            for r in form_rows
        ),
        "typed_compute_self_ii_missing_forms": sum(
            r["scope"] == "typed_or_physical"
            and r["class"] == "compute"
            and r["self_ii"] is None
            for r in form_rows
        ),
        "compute_forms": sum(r["class"] == "compute" for r in form_rows),
        "self_ii_exact_forms": sum(
            r["class"] == "compute" and r["self_ii"] is not None for r in form_rows
        ),
        "forwarding_config_entries": sum(
            len(x) for x in forwarding["forwarding"].values()
        ),
        "ii_config_entries": sum(len(x) for x in ii["InitiationInterval"].values()),
    }
    return {
        "summary": stats,
        "apis": rows,
        "configured_forms": form_rows,
        "excluded_apis": EXCLUDED,
        "config_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in config_paths
        },
    }


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# A5 SIMD VF 指令覆盖清单",
        "",
        "目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、编译目标 `dav-c310-vec`，宏 `__NPU_ARCH__=3101`。",
        "输入清单来自该目标预处理后的 VF 头文件。标量计算、SIMT、Cube、DMA 和标量特殊寄存器配置接口在统计范围外。",
        "带标量源的向量计算仍计入范围。",
        "",
        "[覆盖进度与测量限制](instruction_coverage/a5/progress.md) 给出数值实验、软件展开和时序证据；",
        "[逐签名检查](instruction_coverage/a5/signatures.json) 保存代表性调用的 adapter 校验结果。",
        "签名检查只选择一个合法参数配置；各 mode、mask、舍入和索引分布的数值覆盖分别记录在 benchmark 中。",
        "",
        "## 统计",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
    ]
    names = {
        "cann_api_names": "非标量 VF API 名称",
        "cann_overloads": "去重 CANN 签名",
        "cann_api_forms": "API/form 组合",
        "adapter_validated_overloads": "通过 adapter 检查的签名",
        "target_rejected_apis": "该编译目标拒绝的 API 名称",
        "catalog_opcodes": "Catalog opcode（含虚拟入口）",
        "catalog_physical_forms": "非虚拟 opcode/form（含兼容位宽 form）",
        "typed_physical_forms": "类型及物理辅助 form",
        "latency_exact_forms": "具有显式 latency 的 form",
        "typed_latency_missing_forms": "类型及物理辅助 form 缺少 latency",
        "typed_compute_self_ii_missing_forms": "类型及物理辅助 COMPUTE form 缺少 self-II",
        "forwarding_config_entries": "forwarding 配置 pair（含 wildcard）",
        "ii_config_entries": "II 配置 pair（含 wildcard）",
    }
    lines += [f"| {label} | {summary[key]} |" for key, label in names.items()]
    lines += [
        "",
        "## CCE 接口",
        "",
        "每条展开规则生成独立硬件指令及 RAW 依赖。双向量占用两个普通向量值；VAG 的循环步幅保存在访存地址表达式中。",
        "`physical_lowering_forms` 保存代表性调用生成的物理 form，完整签名、头文件位置和配置哈希见机器清单。",
        "",
        "| API | 类别 | CANN form | adapter 签名 | 状态 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    labels = {
        "full_forms": "已适配",
        "partial_forms": "部分适配",
        "missing": "待适配",
        "target_rejected": "目标编译器拒绝",
    }
    for row in report["apis"]:
        lines.append(
            f"| `{row['api']}` | {row['category']} | {', '.join(row['cann_forms'])} | {row['adapter_validated_overloads']}/{row['overloads']} | {labels[row['catalog_status']]} |"
        )
    lines += [
        "",
        "## 时序配置",
        "",
        "显式配置值与实测覆盖分别记录。机器清单中的 `latency_evidence` 指向对应的实验；缺少该链接的已有配置不能据此认定为新增实测。",
        "`b16/b32` 兼容 form 单列，CANN dtype 使用精确类型键。辅助 byte 访存 form 用于展开规则。",
        "self-forwarding 只适用于具有合法同形 RAW 的指令。异种指令的 forwarding 依靠精确 pair、明确继承和缺失参数回退；",
        "所有合法 producer/consumer 的笛卡尔积尚未逐对测量。GSU 与 VSSTB 的服务参数对应记录的索引/步幅分布。",
        "",
        "| opcode.form | latency | FU / 端口 | self-II | 范围 |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for row in report["configured_forms"]:
        value = lambda k: str(row[k]) if row[k] is not None else "—"
        port = (
            f"{value('exu')}/{value('dispatch_exu')}"
            if row["class"] == "compute"
            else "LSU"
        )
        interval = value("self_ii") if row["class"] == "compute" else "不适用"
        lines.append(
            f"| `{row['key']}` | {value('latency')} | {port} | {interval} | {row['scope']} |"
        )
    lines += [
        "",
        "## 工具链限制",
        "",
        "`VSADD/VSADDS/VSSUB`、`PSLIDE_B8/B16/B32`、`VNOP` 与 `VNOPXN` 的 builtin 被该目标编译器拒绝。",
        "`VNOPXN` 为宏接口，单列在机器清单，未计入函数签名总数。",
        "VSELRV2 和四种 FP16/BF16→FP8 转换缺少 CAModel VALU 条目；VSTUR/VSTAR 的有效状态探针在 CAModel 中超时。",
        "这些项保留语义合同及诊断，未写入伪造的 latency、II 或 forwarding。",
        "",
        "## 排除的接口",
        "",
        *[f"- `{name}`：{reason}。" for name, reason in EXCLUDED.items()],
        "",
        "## 复现",
        "",
        "```bash",
        "python3 tools/validate_a5_cce_signatures.py --out /tmp/a5-signatures",
        "python3 tools/audit_a5_instruction_coverage.py --cann /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1 --signature-report /tmp/a5-signatures/signatures.json --output-dir docs/instruction_coverage/a5",
        "```",
        "",
        "[机器清单](instruction_coverage/a5/coverage.json) 包含所有声明和配置 SHA256。",
        "原始预处理文本放在忽略目录 `audit_artifacts/`。参数获取方法见 [benchmark skill](../skills/vfsim-instruction-benchmark/SKILL.md)。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cann", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--model-revision", default=None, help="Model commit used for this audit"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("docs/instruction_coverage/a5")
    )
    parser.add_argument(
        "--signature-report", type=Path, help="Output of validate_a5_cce_signatures.py"
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = output / "audit_artifacts"
    artifacts.mkdir(exist_ok=True)
    cmd = [
        str(args.cann / "bin/ccec"),
        "--cce-aicore-arch=dav-c310-vec",
        "--cce-aicore-only",
        "-x",
        "cce",
    ]
    proc = subprocess.run(
        cmd + ["-E", "/dev/null"], capture_output=True, text=True, check=True
    )
    macros = subprocess.run(
        cmd + ["-dM", "-E", "/dev/null"], capture_output=True, text=True, check=True
    )
    if "#define __NPU_ARCH__ 3101" not in macros.stdout:
        raise ValueError("Expected A5 target __NPU_ARCH__=3101")
    (artifacts / "headers.ii").write_text(proc.stdout)
    (artifacts / "macros.txt").write_text(macros.stdout)
    (artifacts / "diagnostics.txt").write_text(proc.stderr + macros.stderr)
    entries = declarations(proc.stdout)
    report = analyze(
        entries,
        args.root,
        json.loads(args.signature_report.read_text())
        if args.signature_report
        else None,
    )
    resource = Path(
        subprocess.run(
            [str(args.cann / "bin/ccec"), "-print-resource-dir"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    report["source"] = {
        "cann": str(args.cann),
        "soc": "Ascend950PR_9599",
        "target": "dav-c310-vec",
        "npu_arch": 3101,
        "preprocess_command": cmd + ["-E", "/dev/null"],
        "header_sha256": {
            h: hashlib.sha256((resource / "include" / h).read_bytes()).hexdigest()
            for h in HEADERS
        },
    }
    report["model_revision"] = args.model_revision
    report["macro_interfaces"] = [
        {"api": "vnopxn", "class": "control", "catalog_status": "target_rejected"}
    ]
    (output / ".gitignore").write_text("audit_artifacts/\n")
    (output / "coverage.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    doc = args.root / "docs/a5_instruction_coverage.md"
    doc.write_text(markdown(report))
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {doc}")


if __name__ == "__main__":
    main()
