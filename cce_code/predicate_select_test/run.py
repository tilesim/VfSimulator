"""Build/run the A5 predicate probe; validate CAModel output, not cycle accuracy."""
import argparse
import json
import os
import operator
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from forwarding_source import vadd_gap_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", choices=["predicate_select_fp32", "pset_vadd_i16", "pset_vadd_single",
                                           "pset_vdup_single", "pset_vadd_gap", "predicate_compute", "vintlv", "vdintlv",
                                           "vaxpy_single", "vaxpy_chain", "vaxpy_src_chain", "vaxpy_independent",
                                           "vmov_single", "vmov_chain", "vmov_independent", "vmov_load",
                                           "vmov_axpy_dst", "vmov_axpy_src"],
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
    output_elements = elements * (8 if args.probe in ("vaxpy_independent", "vmov_independent") else 2 if rearrange else 1)
    here = Path(__file__).resolve().parent
    cann = Path(os.environ.get("ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"))
    work = Path(tempfile.mkdtemp(prefix="vfsim-predicate-select-"))
    print(f"Artifacts: {work}", flush=True)
    if args.probe.startswith("vmov_"):
        source = (here.parent / "predicate_select_fp32.cce").read_text()
        start = source.index("        vlds(va,")
        end = source.index("\n    }", start)
        body = "        vdup(va, 1.5f, all, MODE_ZEROING);\n"
        if args.probe == "vmov_load":
            body = "        vlds(va, a, 0, NORM);\n"
        if args.probe in ("vmov_axpy_dst", "vmov_axpy_src"):
            body = ("        vlds(va, a, 0, NORM);\n"
                    "        vlds(vb, b, 0, NORM);\n"
                    + "        vmov(va, va);\n" * 8)
            dst, src = ("va", "vb") if args.probe == "vmov_axpy_dst" else ("vb", "va")
            body += (f"        vaxpy({dst}, {src}, 2.0f, all, MODE_ZEROING);\n"
                     f"        vsts({dst}, out, 0, NORM_B32, all);")
        elif args.probe == "vmov_independent":
            body += "\n".join(
                [f"        vector_f32 d{i};\n        vdup(d{i}, {i+1}.5f, all, MODE_ZEROING);" for i in range(8)]
                + [f"        vmov(d{i}, d{i});" for _ in range(4) for i in range(8)]
                + [f"        vsts(d{i}, out, {64*i}, NORM_B32, all);" for i in range(8)])
        else:
            body += "        vmov(va, va);\n" * (16 if args.probe == "vmov_chain" else 1)
            body += "        vsts(va, out, 0, NORM_B32, all);"
        source = source[:start] + body + source[end:]
        if args.probe == "vmov_independent":
            source = source.replace("out, 0, 1, 256,", "out, 0, 1, 2048,")
        source = source.replace("predicate_select_fp32", args.probe)
        (work / "kernel.cce").write_text(source)
    elif args.probe.startswith("vaxpy_"):
        source = (here.parent / "predicate_select_fp32.cce").read_text()
        start = source.index("        vcmp_gt(")
        end = source.index("\n    }", start)
        repetitions = {"vaxpy_single": 1, "vaxpy_chain": 16, "vaxpy_src_chain": 16,
                       "vaxpy_independent": 4}[args.probe]
        body = (
            "        vaxpy(va, vb, 2.0f, all, MODE_ZEROING);\n" * repetitions
            + "        vsts(va, out, 0, NORM_B32, all);")
        if args.probe == "vaxpy_src_chain":
            body = ("        yes = va;\n        vaxpy(yes, vb, 2.0f, all, MODE_ZEROING);\n"
                    "        vb = yes;\n") * repetitions + "        vsts(vb, out, 0, NORM_B32, all);"
        if args.probe == "vaxpy_independent":
            body = "\n".join(
                [f"        vector_f32 d{i};\n        vlds(d{i}, a, 0, NORM);" for i in range(8)]
                + [f"        vaxpy(d{i}, vb, {i+2}.0f, all, MODE_ZEROING);" for _ in range(repetitions) for i in range(8)]
                + [f"        vsts(d{i}, out, {64*i}, NORM_B32, all);" for i in range(8)])
            source = source.replace("out, 0, 1, 256,", "out, 0, 1, 2048,")
        source = source[:start] + body + source[end:]
        source = source.replace("predicate_select_fp32", args.probe)
        (work / "kernel.cce").write_text(source)
    elif rearrange:
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
    if args.probe.startswith("vmov_"):
        expected = [1.5] * output_elements
        if args.probe == "vmov_load":
            expected = list(a)
        if args.probe == "vmov_independent":
            expected = [j + 1.5 for j in range(8) for _ in range(elements)]
        if args.probe == "vmov_axpy_dst":
            expected = [x + 2*y for x, y in zip(a, b)]
        if args.probe == "vmov_axpy_src":
            expected = [y + 2*x for x, y in zip(a, b)]
    if args.probe.startswith("vaxpy_"):
        expected = [x + 2.0 * repetitions * y for x, y in zip(a, b)]
        if args.probe == "vaxpy_independent":
            expected = [x + (j+2) * repetitions * y for j in range(8) for x, y in zip(a, b)]
        elif args.probe == "vaxpy_src_chain":
            expected = list(b)
            for _ in range(repetitions):
                expected = [x + 2*y for x, y in zip(a, expected)]
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
    formulas = {"vmov_single": "out = vmov(vdup(1.5))",
                "vmov_axpy_dst": "out = vmov(a) + 2*b; 8 dependent moves delay old destination",
                "vmov_axpy_src": "out = b + 2*vmov(a); 8 dependent moves delay source",
                "vmov_load": "out = vmov(vlds(a))",
                "vmov_chain": "out = 16 dependent vmov copies of 1.5",
                "vmov_independent": "8 independent vmov chains: out[j] = j + 1.5",
                "vaxpy_single": "out = a + 2*b", "vaxpy_chain": "out = a + 16*2*b",
                "vaxpy_src_chain": "x = b; repeat 16 times: x = a + 2*x",
                "vaxpy_independent": "8 independent accumulators: out[j] = a + 4*(j+2)*b",
                "vintlv": "out = interleave(a,b)", "vdintlv": "out = even(a+b), odd(a+b)",
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
    if args.probe.startswith("vaxpy_"):
        pattern = re.compile(r"\[info\] \[(\d+)\].*?\(ID: (\d+)\) (RV_\w+)")
        def events(filename):
            return [(int(cycle), int(iid), op) for cycle, iid, op in
                    pattern.findall((work / filename).read_text())]
        issued = events("core0.veccore0.instr_popped_log.dump")
        done = {iid: cycle for cycle, iid, _ in events("core0.veccore0.instr_log.dump")}
        axpy = [(cycle, iid) for cycle, iid, op in issued if op == "RV_VAXPY"]
        expected_count = repetitions * (8 if args.probe == "vaxpy_independent" else 1)
        if len(axpy) != expected_count:
            raise AssertionError(f"Expected {expected_count} RV_VAXPY, compiled {len(axpy)}")
        report["vaxpy_timing"] = {
            "instruction_count": len(axpy),
            "latencies": sorted({done[iid] - cycle for cycle, iid in axpy}),
            "start_cycles": [cycle for cycle, _ in axpy],
            "adjacent_start_gaps": [b[0] - a[0] for a, b in zip(axpy, axpy[1:])],
        }
    if args.probe.startswith("vmov_"):
        pattern = re.compile(r"\[info\] \[(\d+)\].*?\(ID:\s*(\d+)\)\s+(RV_\w+)")
        issued = [(int(c), int(i), op) for c, i, op in
                  pattern.findall((work / "core0.veccore0.instr_popped_log.dump").read_text())]
        done = {int(i): int(c) for c, i, _ in
                pattern.findall((work / "core0.veccore0.instr_log.dump").read_text())}
        moves = [(c, i) for c, i, op in issued if op == "RV_VMOV"]
        expected_count = {"vmov_single": 1, "vmov_chain": 16, "vmov_independent": 32,
                          "vmov_load": 1, "vmov_axpy_dst": 8, "vmov_axpy_src": 8}[args.probe]
        if len(moves) != expected_count:
            raise AssertionError(f"Expected {expected_count} RV_VMOV, compiled {len(moves)}")
        report["vmov_timing"] = {
            "instruction_count": len(moves),
            "latencies": sorted({done[i] - c for c, i in moves}),
            "start_cycles": [c for c, _ in moves],
            "adjacent_start_gaps": [b[0] - a[0] for a, b in zip(moves, moves[1:])],
        }
        if args.probe == "vmov_load":
            loads = [(c, i) for c, i, op in issued if op == "RV_VLDI"]
            if len(loads) != 1:
                raise AssertionError(f"Expected one RV_VLDI, compiled {len(loads)}")
            report["vmov_timing"]["vlds_to_vmov_start_gap"] = moves[0][0] - loads[0][0]
        if args.probe in ("vmov_axpy_dst", "vmov_axpy_src"):
            axpy = [(c, i) for c, i, op in issued if op == "RV_VAXPY"]
            if len(axpy) != 1:
                raise AssertionError(f"Expected one RV_VAXPY, compiled {len(axpy)}")
            report["vmov_timing"]["vmov_to_vaxpy_start_gap"] = axpy[0][0] - moves[-1][0]
    (work / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    if mismatches:
        raise AssertionError("CAModel numerical validation failed")


if __name__ == "__main__":
    main()
