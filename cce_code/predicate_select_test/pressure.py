"""Sweep simultaneous predicate live values in an eight-iteration A5 VF."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


def source(n):
    declarations = "\n".join(f"        vector_bool p{k};" for k in range(n))
    comparisons = "\n".join(
        f"            vcmps_gt(p{k}, a, {k - n // 2}.0f, all);" for k in range(n))
    consumers = "\n".join(
        f"            vsel(selected, one, zero, p{k});\n"
        "            vadd(acc, acc, selected, all, MODE_ZEROING);" for k in range(n))
    return f'''#define __aicore__ [aicore]
__attribute__((always_inline)) inline [aicore] void pressure_vf(
    __ubuf__ float *input, __ubuf__ float *out) {{
    __VEC_SCOPE__ {{
        vector_bool all = pset_b32(PAT_ALL);
{declarations}
        vector_f32 a, one, zero, selected, acc;
        vdup(one, 1.0f, all, MODE_ZEROING);
        vdup(zero, 0.0f, all, MODE_ZEROING);
        vdup(acc, 0.0f, all, MODE_ZEROING);
        #pragma unroll 1
        for (int i = 0; i < 8; ++i) {{
            vlds(a, input, i * 64, NORM);
{comparisons}
{consumers}
        }}
        vsts(acc, out, 0, NORM_B32, all);
    }}
}}
extern "C" __global__ __aicore__ void predicate_pressure(
    __gm__ float *input, __gm__ float *output) {{
    __ubuf__ float *in = (__ubuf__ float *)get_imm(0);
    __ubuf__ float *out = (__ubuf__ float *)get_imm(0x4000);
    copy_gm_to_ubuf_align_v2(in, input, 0, 1, 2048, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    pressure_vf(in, out);
    set_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    wait_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    copy_ubuf_to_gm_align_v2(output, out, 0, 1, 256, 0, 0, 0);
}}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[4, 6, 7, 8, 12, 16, 24, 32])
    parser.add_argument("--run", type=int, nargs="*", default=[])
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    cann = Path(os.environ.get("ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"))
    root = Path(tempfile.mkdtemp(prefix="vfsim-predicate-pressure-"))
    print(f"Artifacts: {root}", flush=True)
    libs = [cann / "tools/simulator/Ascend950PR_9599/lib", cann / "lib64",
            cann / "x86_64-linux/devlib", cann / "x86_64-linux/devlib/device"]
    env = dict(os.environ, ASCEND_TOOLKIT_HOME=str(cann), NPU_TYPE="Ascend950PR_9599",
               LD_LIBRARY_PATH=":".join(map(str, libs)))
    includes = [cann / "x86_64-linux/include", cann / "include",
                cann / "x86_64-linux/include/experiment/msprof",
                cann / "x86_64-linux/include/experiment/msprof/toolchain",
                cann / "x86_64-linux/pkg_inc/profiling",
                cann / "x86_64-linux/pkg_inc", cann / "x86_64-linux/pkg_inc/runtime"]
    commands = []

    def run(argv, cwd, log):
        argv = list(map(str, argv))
        commands.append(dict(argv=argv, cwd=str(cwd)))
        (root / "commands.json").write_text(json.dumps(commands, indent=2))
        with (cwd / log).open("w") as f:
            proc = subprocess.run(argv, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=180)
        if proc.returncode:
            raise RuntimeError(f"{cwd / log}: exit {proc.returncode}")

    shutil.copy2(here / "host.cpp", root / "host.cpp")
    if args.run:
        run([cann / "bin/bisheng", "-std=c++17", "-O2", "-DPROBE_INPUT_BYTES=2048",
             "host.cpp", "-o", "host", "-Wl,--allow-shlib-undefined",
             *[f"-I{x}" for x in includes], *[f"-L{x}" for x in libs],
             "-lruntime_camodel", "-lstdc++", "-lascendcl", "-lm", "-ltiling_api",
             "-lplatform", "-lc_sec", "-ldl", "-lnnopbase"], root, "host_build.log")
    summary = []
    for n in args.counts:
        case = root / f"n{n}"
        case.mkdir()
        (case / "predicate_pressure.cce").write_text(source(n))
        flags = ["-g", "-std=c++17", "-O2", "-I/usr/include/c++/11",
                 "-I/usr/include/aarch64-linux-gnu/c++/11", "--cce-aicore-arch=dav-c310-vec",
                 "--cce-aicore-only", "--cce-simd-vf-fusion=false",
                 "-mllvm", "-cce-aicore-vec-misched=0"]
        report = dict(masks=n, iterations=8)
        try:
            run([cann / "bin/ccec", *flags, "-c", "predicate_pressure.cce",
                 "-o", "kernel_aiv.o"], case, "compile.log")
            report["compiled"] = True
            if n in args.run:
                run([cann / "bin/ld.lld", "-Ttext=0", "kernel_aiv.o", "-static", "-o", "kernel.o"], case, "link.log")
                values = [(j - 32) * 0.5 + i * 0.25 for i in range(8) for j in range(64)]
                expected = [sum(values[i*64+j] > k - n//2 for i in range(8) for k in range(n)) for j in range(64)]
                reference = struct.pack("<64f", *expected)
                (case / "input.bin").write_bytes(struct.pack("<512f", *values))
                (case / "golden.bin").write_bytes(reference)
                run([root / "host", case / "kernel.o", "predicate_pressure"], case, "run.log")
                actual = (case / "output.bin").read_bytes()
                report["numerical_pass"] = actual == reference
                report["mismatches"] = [j for j in range(64) if actual[j*4:j*4+4] != reference[j*4:j*4+4]]
                idu = (case / "core0.veccore0.rvec.IDU.dump").read_text()
                free = [int(x) for x in re.findall(r"ooo=\(preg:(\d+)", idu)]
                report["min_free_preg"] = min(free) if free else None
                report["preg_block_lines"] = [line for line in idu.splitlines() if "REASON:" in line and re.search(r"REASON:.*(?:preg|PREG|predicate)", line)]
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            report["error"] = str(exc)
        summary.append(report)
        (root / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps({key: value for key, value in report.items()
                          if key != "preg_block_lines"}), flush=True)
    if any("error" in r or r.get("numerical_pass") is False for r in summary):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
