"""CAModel semantic/timing probes for multi-result and alignment-state LSU ops."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


def make_case(kind, dtype, offset, iterations, active_elements=37):
    types = {"fp32": ("float", "f32", "f", 4),
             "fp16": ("half", "f16", "e", 2),
             "int32": ("int32_t", "s32", "i", 4)}
    ctype, vector, fmt, width = types[dtype]
    lanes = 256 // width
    allocation = iterations * 1024 if kind in {"dual_pairs", "norm_pairs"} else 1024
    values = [i % 251 - 125 for i in range(allocation // width)]
    data = struct.pack(f"<{len(values)}{fmt}", *values)
    init_output = ""
    if kind.startswith("predicate_"):
        if dtype != "fp32" or iterations != 1:
            raise ValueError("Predicate spill probes require fp32, iterations=1")
        # A B32 predicate uses one bit per four byte lanes; preserve all 256 bits.
        mask = sum(1 << (4 * i) for i in range(0, 64, 2)).to_bytes(32, "little")
        data = data[:768] + mask + data[800:]
        prefix = '''vector_f32 x,y,z;
        vector_bool all=pset_b32(PAT_ALL),p,q;
        vlds(x,input,0,NORM);vlds(y,input,64,NORM);
        __ubuf__ uint32_t *bits=(__ubuf__ uint32_t *)input;
        __ubuf__ uint32_t *saved=(__ubuf__ uint32_t *)out;'''
        if kind == "predicate_compare_store":
            body = prefix + "vcmp_eq(p,x,x,all);psts(p,saved,32,NORM);"
            init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
            golden = data[:32] + bytes([0x11])*32 + data[64:256]
        elif kind.startswith("predicate_pset_store"):
            bits = 32 if kind.endswith("32") else 16 if kind.endswith("16") else 8
            body = f"vector_bool p=pset_b{bits}(PAT_ALL);__ubuf__ uint32_t *saved=(__ubuf__ uint32_t *)out;psts(p,saved,32,NORM);"
            init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
            golden = data[:32] + sum(1 << i for i in range(0,256,bits//8)).to_bytes(32,"little") + data[64:256]
        else:
            body = prefix + "plds(p,bits,768,NORM);"
            if kind == "predicate_load_store":
                body += "psts(p,saved,32,NORM);"
                init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
                golden = data[:32] + mask + data[64:256]
            elif kind == "predicate_load_masked_store":
                init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
                body += "vsts(y,out,0,NORM_B32,p);vsts(x,out,64,NORM_B32,all);"
                golden = struct.pack("<64f", *[values[64+i] if i%2==0 else values[i] for i in range(64)]) + data[:256]
            else:
                if kind == "predicate_load_compute":
                    body += "vadds(z,x,1.0f,p,MODE_ZEROING);"
                    result = [values[i]+1 if i%2==0 else 0 for i in range(64)]
                else:
                    if kind == "predicate_load_logic":
                        body += "pand(q,p,p,all);"
                    if kind == "predicate_load_compare":
                        body += "vcmp_eq(q,x,x,p);"
                    body += f"vsel(z,x,y,{'q' if kind in ('predicate_load_logic','predicate_load_compare') else 'p'});"
                    result = [values[i] if i%2==0 else values[64+i] for i in range(64)]
                body += "vsts(z,out,0,NORM_B32,all);"
                golden = struct.pack("<64f", *result)
    elif kind == "unpack_pack_tail":
        if dtype != "fp16" or offset != 0 or iterations != 1:
            raise ValueError("Packed tail probe requires fp16, offset=0, iterations=1")
        init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
        if not 0 <= active_elements <= 64:
            raise ValueError("Packed probe active elements must be between 0 and 64")
        body = f'''uint32_t count = {active_elements};
        vector_bool tail = plt_b32(count, POST_UPDATE);
        vector_f16 value;
        vlds(value, input, 64, UNPK_B16);
        vsts(value, out, 0, PK_B32, tail);'''
        written_bytes = active_elements * 2
        golden = data[128:128+written_bytes] + data[written_bytes:256]
    elif kind in {"dual_pairs", "norm_pairs"}:
        if dtype != "fp32" or offset != 0:
            raise ValueError("Paired throughput probe requires FP32, offset zero")
        dual = kind == "dual_pairs"
        stride = 256 if dual else 128
        loads = ("vlds(a,b,input,i*256,DINTLV_B32);\n"
                 "vlds(c,d,input,i*256+128,DINTLV_B32);" if dual else
                 "vlds(a,input,i*128,NORM);\nvlds(c,input,i*128+64,NORM);")
        compute = "vadds(a,a,1.0f,all);vadds(c,c,1.0f,all);"
        stores = f"vsts(a,out,i*{stride},NORM_B32,all);vsts(c,out,i*{stride}+{128 if dual else 64},NORM_B32,all);"
        if dual:
            compute += "vadds(b,b,1.0f,all);vadds(d,d,1.0f,all);"
            stores += "vsts(b,out,i*256+64,NORM_B32,all);vsts(d,out,i*256+192,NORM_B32,all);"
        body = f'''vector_bool all=pset_b32(PAT_ALL);
        vector_f32 a,b,c,d;
        #pragma unroll 1
        for(int i=0;i<{iterations};++i) {{
          {loads}
          {compute}
          {stores}
        }}'''
        expected = []
        for i in range(iterations):
            block = values[i*stride:(i+1)*stride]
            if dual:
                block = block[:128:2] + block[1:128:2] + block[128::2] + block[129::2]
            expected.extend(v + 1 for v in block)
        golden = struct.pack(f"<{len(expected)}f", *expected)
    elif kind == "vldsx2":
        if dtype != "fp32":
            raise ValueError("First VLDSX2 probe covers FP32/DINTLV_B32 only")
        if offset + 128 > len(values):
            raise ValueError("Input allocation too small")
        body = f'''vector_bool all = pset_b32(PAT_ALL);
        vector_f32 even, odd;
        vlds(even, odd, input, {offset}, DINTLV_B32);
        vsts(even, out, 0, NORM_B32, all);
        vsts(odd, out, 64, NORM_B32, all);'''
        selected = values[offset:offset + 128]
        golden = struct.pack("<128f", *(selected[::2] + selected[1::2]))
    elif kind in {"vldus", "vldus_straight", "vldus_no_update"}:
        if kind == "vldus_no_update" and iterations != 1:
            raise ValueError("Non-updating initialization probe requires one access")
        if offset * width + iterations * 256 > len(data):
            raise ValueError("Input allocation too small")
        accesses = (f'''#pragma unroll 1
        for (int i = 0; i < {iterations}; ++i) {{
            vldus(value, state, ptr, {lanes}, POST_UPDATE);
            vsts(value, out, i * {lanes}, NORM_B{width * 8}, all);
        }}''' if kind == "vldus" else "\n".join(
            f"vldus(value,state,ptr,{lanes},POST_UPDATE);"
            f"vsts(value,out,{i*lanes},NORM_B{width*8},all);"
            for i in range(iterations)))
        if kind == "vldus_no_update":
            accesses = f"vldus(value,state,ptr);vsts(value,out,0,NORM_B{width*8},all);"
        body = f'''vector_bool all = pset_b{width * 8}(PAT_ALL);
        vector_align state;
        vector_{vector} value;
        __ubuf__ {ctype} *ptr = input + {offset};
        vldas(state, ptr);
        {accesses}'''
        golden = data[offset * width:offset * width + iterations * 256]
    else:
        if dtype not in ("fp32", "fp16"):
            raise ValueError("PSTU probe uses fp32/fp16 as B32/B16 width selectors")
        ctype = f"uint{width * 8}_t"
        data = b"\xa5" * 1024
        init_output = "copy_gm_to_ubuf_align_v2(out, inputGM, 0, 1, 256, 0, 0, 0, 0, 0, 0);"
        body = f'''vector_align state;
        vector_bool mask = pset_b{width * 8}(PAT_VL32);
        __ubuf__ {ctype} *ptr = out + {offset};
        #pragma unroll 1
        for (int i = 0; i < {iterations}; ++i) {{
            pstu(state, mask, ptr);
        }}
        vstas(state, ptr, 0, POST_UPDATE);'''
        # Hypothesis checked against all bytes, including untouched sentinel guards.
        packed = ((1 << 32) - 1).to_bytes(lanes // 8, "little") * iterations
        golden = bytearray(b"\xa5" * 256)
        start = offset * width
        if start + len(packed) > len(golden):
            raise ValueError("Output allocation too small")
        golden[start:start + len(packed)] = packed
        golden = bytes(golden)
    source = f'''#define __aicore__ [aicore]
__attribute__((always_inline)) inline [aicore] void memory_probe_vf(
    __ubuf__ {ctype} *input, __ubuf__ {ctype} *out) {{
    __VEC_SCOPE__ {{
        {body}
    }}
}}
extern "C" __global__ __aicore__ void memory_probe(
    __gm__ {ctype} *inputGM, __gm__ {ctype} *outputGM) {{
    __ubuf__ {ctype} *input = (__ubuf__ {ctype} *)get_imm(0);
    __ubuf__ {ctype} *out = (__ubuf__ {ctype} *)get_imm(0x4000);
    copy_gm_to_ubuf_align_v2(input, inputGM, 0, 1, {len(data)}, 0, 0, 0, 0, 0, 0);
    {init_output}
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    memory_probe_vf(input, out);
    set_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    wait_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    copy_ubuf_to_gm_align_v2(outputGM, out, 0, 1, {len(golden)}, 0, 0, 0);
}}
'''
    return source, data, golden


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["vldsx2", "vldus", "vldus_straight", "vldus_no_update", "pstu", "dual_pairs", "norm_pairs", "unpack_pack_tail", "predicate_compare_store", "predicate_pset_store", "predicate_pset_store16", "predicate_pset_store32", "predicate_load_store", "predicate_load_select", "predicate_load_compute", "predicate_load_logic", "predicate_load_compare", "predicate_load_masked_store"], required=True)
    parser.add_argument("--dtype", choices=["fp32", "fp16", "int32"], default="fp32")
    parser.add_argument("--offset", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--active-elements", type=int, default=37)
    args = parser.parse_args()
    if args.offset < 0 or args.iterations < 1:
        parser.error("offset must be nonnegative and iterations positive")
    source, data, golden = make_case(args.kind, args.dtype, args.offset, args.iterations, args.active_elements)
    work = Path(tempfile.mkdtemp(prefix="vfsim-memory-probe-"))
    print(f"Artifacts: {work}", flush=True)
    (work / "kernel.cce").write_text(source)
    (work / "input.bin").write_bytes(data)
    (work / "golden.bin").write_bytes(golden)
    shutil.copy2(Path(__file__).with_name("host.cpp"), work / "host.cpp")
    cann = Path(os.environ.get("ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"))
    libs = [cann / "tools/simulator/Ascend950PR_9599/lib", cann / "lib64",
            cann / "x86_64-linux/devlib", cann / "x86_64-linux/devlib/device"]
    includes = [cann / p for p in ["x86_64-linux/include", "include",
                "x86_64-linux/include/experiment/msprof", "x86_64-linux/include/experiment/msprof/toolchain",
                "x86_64-linux/pkg_inc/profiling", "x86_64-linux/pkg_inc", "x86_64-linux/pkg_inc/runtime"]]
    env = dict(os.environ, ASCEND_TOOLKIT_HOME=str(cann), NPU_TYPE="Ascend950PR_9599",
               LD_LIBRARY_PATH=":".join(map(str, libs)))
    commands = []

    def run(argv, log):
        argv = list(map(str, argv))
        commands.append(argv)
        (work / "commands.json").write_text(json.dumps(commands, indent=2))
        with (work / log).open("w") as output:
            subprocess.run(argv, cwd=work, env=env, stdout=output, stderr=subprocess.STDOUT,
                           timeout=180, check=True)

    run([cann / "bin/bisheng", "-std=c++17", "-O2", "host.cpp", "-o", "host",
         f"-DPROBE_INPUT_BYTES={len(data)}", f"-DPROBE_OUTPUT_BYTES={len(golden)}",
         "-Wl,--allow-shlib-undefined", *[f"-I{x}" for x in includes], *[f"-L{x}" for x in libs],
         "-lruntime_camodel", "-lstdc++", "-lascendcl", "-lm", "-ltiling_api", "-lplatform",
         "-lc_sec", "-ldl", "-lnnopbase"], "host_build.log")
    run([cann / "bin/ccec", "-g", "-std=c++17", "-O2", "-c", "kernel.cce", "-o", "kernel_aiv.o",
         "-I/usr/include/c++/11", "-I/usr/include/aarch64-linux-gnu/c++/11",
         "--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only", "--cce-simd-vf-fusion=false",
         "-mllvm", "-cce-aicore-vec-misched=0"], "compile.log")
    run([cann / "bin/ld.lld", "-Ttext=0", "kernel_aiv.o", "-static", "-o", "kernel.o"], "link.log")
    run([work / "host", work / "kernel.o", "memory_probe"], "run.log")
    actual = (work / "output.bin").read_bytes()
    mismatches = [i for i, (a, b) in enumerate(zip(actual, golden)) if a != b]
    report = dict(vars(args), passed=actual == golden,
                  comparison="bytewise equality including guards" if args.kind == "pstu" else "bytewise equality",
                  input_bytes=len(data), output_bytes=len(actual), mismatch_bytes=mismatches)
    if args.kind == "vldsx2":
        report["iterations"] = 1
    popped = (work / "core0.veccore0.instr_popped_log.dump").read_text()
    done = (work / "core0.veccore0.instr_log.dump").read_text()
    pattern = r"\[info\] \[(\d+)\].*?\(ID: (\d+)\) (RV_\w+)(.*)"
    finishes = {(int(m[2]), m[3]): int(m[1]) for m in re.finditer(pattern, done)}
    report["events"] = [dict(op=m[3], id=int(m[2]), issue=int(m[1]),
                             done=finishes.get((int(m[2]), m[3])), operands=m[4])
                        for m in re.finditer(pattern, popped)]
    report["vf_cycles"] = int(re.search(r"vf_execute_time: (\d+)", done)[1])
    (work / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "events"}), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
