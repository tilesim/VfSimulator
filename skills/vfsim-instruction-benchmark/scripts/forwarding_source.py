"""Generate an observable independent-instruction gap before the target PSET."""


def vadd_gap_source(gap, target_pattern="PAT_VL32"):
    filler_pattern = "PAT_VL32" if target_pattern == "PAT_ALL" else "PAT_ALL"
    declarations = "\n".join(f"        vector_f32 pad{k};" for k in range(gap))
    filler = "\n".join(
        f"        vadds(pad{k}, a, {k + 1}.0f, all, MODE_ZEROING);\n"
        f"        vsts(pad{k}, scratch, {k * 64}, NORM_B32, all);"
        for k in range(gap)
    )
    return f'''#define __aicore__ [aicore]
__attribute__((always_inline)) inline [aicore] void pset_vadd_gap_vf(
    __ubuf__ float *input, __ubuf__ float *out, __ubuf__ float *scratch) {{
    __VEC_SCOPE__ {{
        vector_bool all = pset_b32({filler_pattern});
        vector_f32 a, b, result;
{declarations}
        vlds(a, input, 0, NORM);
        vlds(b, input, 64, NORM);
{filler}
        vector_bool target = pset_b32({target_pattern});
        vadd(result, a, b, target, MODE_ZEROING);
        vector_bool store_mask = pset_b32(PAT_ALL);
        vsts(result, out, 0, NORM_B32, store_mask);
    }}
}}
extern "C" __global__ __aicore__ void pset_vadd_gap(
    __gm__ float *input, __gm__ float *output) {{
    __ubuf__ float *in = (__ubuf__ float *)get_imm(0);
    __ubuf__ float *out = (__ubuf__ float *)get_imm(0x4000);
    __ubuf__ float *scratch = (__ubuf__ float *)get_imm(0x8000);
    copy_gm_to_ubuf_align_v2(in, input, 0, 1, 512, 0, 0, 0, 0, 0, 0);
    set_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    wait_flag(PIPE_MTE2, PIPE_V, (event_t)0);
    pset_vadd_gap_vf(in, out, scratch);
    set_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    wait_flag(PIPE_V, PIPE_MTE3, (event_t)0);
    copy_ubuf_to_gm_align_v2(output, out, 0, 1, 256, 0, 0, 0);
}}
'''
