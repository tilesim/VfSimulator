"""Build/run the A5 predicate probe; validate CAModel output, not cycle accuracy."""
import argparse
import json
import os
import operator
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from forwarding_source import vadd_gap_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", choices=["predicate_select_fp32", "pset_vadd_i16", "pset_vadd_single",
                                           "pset_vdup_single", "pset_vadd_gap", "predicate_compute", "vintlv", "vdintlv"],
                        default="predicate_select_fp32")
    parser.add_argument("--gap", type=int, choices=range(25), default=0)
    parser.add_argument("--reverse-stores", action="store_true", help="Reverse the two rearrangement output stores")
    parser.add_argument("--target-pattern", choices=["PAT_ALL", "PAT_VL32"], default="PAT_VL32")
    parser.add_argument("--operation", choices=[f"{family}_{condition}" for family in ("vcmp", "vcmps")
                                                for condition in ("eq", "ne", "gt", "ge", "lt", "le")]
                        + ["pand", "por", "movvp_ones", "movvp_zeros", "movvp_b16_ones", "movvp_b16_zeros"], default="vcmp_gt")
    args = parser.parse_args()
    elements = 1024 if args.probe == "pset_vadd_i16" else 64
    rearrange = args.probe in ("vintlv", "vdintlv")
    output_elements = elements * 2 if rearrange else elements
    here = Path(__file__).resolve().parent
    cann = Path(os.environ.get("ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"))
    work = Path(tempfile.mkdtemp(prefix="vfsim-predicate-select-"))
    print(f"Artifacts: {work}", flush=True)
    if rearrange:
        source = (here.parent / "predicate_select_fp32.cce").read_text()
        start = source.index("        vcmp_gt(")
        end = source.index("\n    }", start)
        source = source[:start] + (
            f"        {args.probe}(yes, no, va, vb);\n"
            "        vsts(yes, out, 0, NORM_B32, all);\n"
            "        vsts(no, out, 64, NORM_B32, all);") + source[end:]
        if args.reverse_stores:
            source = source.replace(
                "vsts(yes, out, 0, NORM_B32, all);\n        vsts(no, out, 64, NORM_B32, all);",
                "vsts(no, out, 64, NORM_B32, all);\n        vsts(yes, out, 0, NORM_B32, all);")
        source = source.replace("predicate_select_fp32", args.probe + "_probe")
        source = source.replace("out, 0, 1, 256,", "out, 0, 1, 512,")
        (work / "kernel.cce").write_text(source)
    elif args.probe == "pset_vadd_gap":
        (work / "kernel.cce").write_text(vadd_gap_source(args.gap, args.target_pattern))
    elif args.probe == "predicate_compute":
        source = (here.parent / "predicate_select_fp32.cce").read_text()
        if args.operation.startswith("movvp_"):
            b16 = args.operation.startswith("movvp_b16_")
            bits = ("0xffffu" if b16 else "0xffffffffu") if args.operation.endswith("_ones") else "0u"
            replacement = (f"vector_u{16 if b16 else 32} bits;\n"
                           f"        vector_bool bits_all = pset_b{16 if b16 else 32}(PAT_ALL);\n"
                           f"        vdup(bits, {bits}, bits_all, MODE_ZEROING);\n"
                           "        movvp(greater, bits, 0);")
        elif args.operation in ("pand", "por"):
            replacement = ("vector_bool left, right;\n"
                           "        vcmp_gt(left, va, vb, all);\n"
                           "        vcmp_lt(right, va, vb, all);\n"
                           f"        {args.operation}(greater, left, right, all);")
        else:
            rhs = "0.0f" if args.operation.startswith("vcmps_") else "vb"
            replacement = f"{args.operation}(greater, va, {rhs}, all);"
        source = source.replace("vcmp_gt(greater, va, vb, all);", replacement)
        source = source.replace("predicate_select_fp32", "predicate_compute")
        (work / "kernel.cce").write_text(source)
    else:
        shutil.copy2(here.parent / f"{args.probe}.cce", work / "kernel.cce")
    shutil.copy2(here / "host.cpp", work / "host.cpp")
    libs = [cann / "tools/simulator/Ascend950PR_9599/lib", cann / "lib64",
            cann / "x86_64-linux/devlib", cann / "x86_64-linux/devlib/device"]
    env = dict(os.environ, ASCEND_TOOLKIT_HOME=str(cann), NPU_TYPE="Ascend950PR_9599",
               LD_LIBRARY_PATH=":".join(map(str, libs)))
    commands = []

    def run(argv, name):
        argv = list(map(str, argv))
        commands.append(argv)
        with (work / name).open("w") as log:
            result = subprocess.run(argv, cwd=work, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=180)
        if result.returncode:
            raise RuntimeError(f"{name}: {result.returncode}\n{(work / name).read_text()}")

    a = [(i - 32) * 0.25 for i in range(elements)]
    b = [x + (-0.5, 0.5, 0.0)[i % 3] for i, x in enumerate(a)]
    expected = [x + 1.0 if x > y else y - 1.0 for x, y in zip(a, b)]
    if args.probe in ("pset_vadd_i16", "pset_vadd_single"):
        expected = [x + y for x, y in zip(a, b)]
    if args.probe == "pset_vdup_single":
        expected = [1.0] * elements
    if args.probe == "pset_vadd_gap":
        expected = [x + y if args.target_pattern == "PAT_ALL" or i < 32 else 0.0
                    for i, (x, y) in enumerate(zip(a, b))]
    if args.probe == "predicate_compute":
        if args.operation.startswith("movvp_"):
            selected = [args.operation.endswith("_ones")] * elements
        elif args.operation in ("pand", "por"):
            combine = operator.and_ if args.operation == "pand" else operator.or_
            selected = [combine(x > y, x < y) for x, y in zip(a, b)]
        else:
            compare = getattr(operator, args.operation.split("_")[1])
            selected = [compare(x, 0.0 if args.operation.startswith("vcmps_") else y)
                        for x, y in zip(a, b)]
        expected = [x + 1.0 if sel else y - 1.0 for x, y, sel in zip(a, b, selected)]
    if rearrange:
        expected = ([value for pair in zip(a, b) for value in pair] if args.probe == "vintlv"
                    else (a + b)[::2] + (a + b)[1::2])
    (work / "input.bin").write_bytes(struct.pack(f"<{2 * elements}f", *(a + b)))
    reference = struct.pack(f"<{output_elements}f", *expected)
    (work / "golden.bin").write_bytes(reference)
    includes = [cann / "x86_64-linux/include", cann / "include",
                cann / "x86_64-linux/include/experiment/msprof",
                cann / "x86_64-linux/include/experiment/msprof/toolchain",
                cann / "x86_64-linux/pkg_inc/profiling",
                cann / "x86_64-linux/pkg_inc", cann / "x86_64-linux/pkg_inc/runtime"]
    run([cann / "bin/bisheng", "-std=c++17", "-O2", "host.cpp", "-o", "host",
         f"-DPROBE_INPUT_BYTES={elements * 8}", f"-DPROBE_OUTPUT_BYTES={output_elements * 4}",
         "-Wl,--allow-shlib-undefined", *[f"-I{x}" for x in includes],
         *[f"-L{x}" for x in libs], "-lruntime_camodel", "-lstdc++", "-lascendcl",
         "-lm", "-ltiling_api", "-lplatform", "-lc_sec", "-ldl", "-lnnopbase"], "host_build.log")
    run([cann / "bin/ccec", "-g", "-std=c++17", "-c", "-O2", "kernel.cce", "-o", "kernel_aiv.o",
         "-I/usr/include/c++/11", "-I/usr/include/aarch64-linux-gnu/c++/11",
         "--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only",
         "--cce-simd-vf-fusion=false", "-mllvm", "-cce-aicore-vec-misched=0"], "compile.log")
    run([cann / "bin/ld.lld", "-Ttext=0", "kernel_aiv.o", "-static", "-o", "kernel.o"], "link.log")
    run([work / "host", work / "kernel.o", args.probe + "_probe" if rearrange else args.probe], "run.log")
    actual_bytes = (work / "output.bin").read_bytes()
    actual = struct.unpack(f"<{output_elements}f", actual_bytes)
    mismatches = [i for i in range(output_elements) if actual_bytes[4*i:4*i+4] != reference[4*i:4*i+4]]
    formulas = {"vintlv": "out = interleave(a,b)", "vdintlv": "out = even(a+b), odd(a+b)",
                "pset_vdup_single": "out = 1",
                "predicate_compute": f"out = {args.operation}(a, b or 0) ? a + 1 : b - 1",
                "pset_vadd_gap": "out = a + b" if args.target_pattern == "PAT_ALL" else
                                 "out[lane] = lane < 32 ? a + b : 0",
                "predicate_select_fp32": "out = (a > b) ? a + 1 : b - 1"}
    report = dict(probe=args.probe, operation=args.operation, gap=args.gap,
                  reverse_stores=args.reverse_stores,
                  target_pattern=args.target_pattern if args.probe == "pset_vadd_gap" else "PAT_ALL",
                  passed=not mismatches, elements=output_elements, mismatch_indices=mismatches,
                  max_abs_error=max(abs(x-y) for x,y in zip(actual, expected)),
                  greater_lanes=sum(x > y for x,y in zip(a,b)),
                  less_lanes=sum(x < y for x,y in zip(a,b)),
                  equal_lanes=sum(x == y for x,y in zip(a,b)),
                  comparison="FP32 bitwise equality", soc="A5 / dav-c310-vec",
                  formula=formulas.get(args.probe, "out = a + b"), commands=commands)
    (work / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    if mismatches:
        raise AssertionError("CAModel numerical validation failed")


if __name__ == "__main__":
    main()
