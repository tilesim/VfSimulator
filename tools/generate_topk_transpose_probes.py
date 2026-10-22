"""Generate source-faithful A5 probes; golden checks are performed on raw bytes."""
from pathlib import Path
import argparse

HEADER = '#define __aicore__ [aicore]\n'

def kernel(body, *, prelude='', dtype='half'):
    return HEADER + f'''
extern "C" __global__ __aicore__ void probe(__gm__ float *input, __gm__ float *output) {{
    __ubuf__ {dtype} *src = (__ubuf__ {dtype} *)get_imm(0);
    __ubuf__ {dtype} *dst = (__ubuf__ {dtype} *)get_imm(0x10000);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)src,input,0,1,32768,0,0,0,0,0,0);
    copy_gm_to_ubuf_align_v2((__ubuf__ float*)dst,input,0,1,32768,0,0,0,0,0,0);
    set_flag(PIPE_MTE2,PIPE_V,(event_t)0); wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
    {prelude}
    {body}
    pipe_barrier(PIPE_ALL);
    copy_ubuf_to_gm_align_v2(output,(__ubuf__ float*)dst,0,1,32768,0,0,0);
    pipe_barrier(PIPE_ALL);
}}
'''

def generate(out):
    out.mkdir(parents=True, exist_ok=True)
    cases = {}
    for rows, cols in ((64,128),(128,128)):
        gather = rows >= cols
        extent, stride = (cols,cols) if gather else (rows,rows)
        memory = ('vgather2(data,src,idx,p); vsts(data,dst,i*128,NORM_B16,p);'
                  if gather else 'vscatter(data,dst,idx,p);')
        load = '' if gather else 'vlds(data,src,i*128,NORM);'
        cases[f'transpose_{rows}x{cols}'] = kernel(f'''__VEC_SCOPE__ {{
            vector_bool p=pset_b16(PAT_ALL); vector_u16 idx; vector_f16 data;
            for(uint16_t i=0;i<{extent};++i) {{
                {load}
                vci((vector_s16&)idx,(int16_t)0,INC_ORDER);
                vmins(idx,idx,(uint16_t)127,p);
                vmuls(idx,idx,(uint16_t){stride},p); vadds(idx,idx,i,p);
                {memory}
            }}
        }}''')
    for op in ('gather','scatter'):
        for stride in (1,2,16,64,128):
            for count in (32,128):
                mem = ('vgather2(data,src,idx,p); vsts(data,dst,0,NORM_B16,p);'
                       if op=='gather' else 'vlds(data,src,0,NORM); vscatter(data,dst,idx,p);')
                cases[f'{op}_s{stride}_n{count}'] = kernel(f'''__VEC_SCOPE__ {{
                    vector_bool p=pset_b16({'PAT_ALL' if count==128 else 'PAT_VL32'});
                    vector_u16 idx; vector_f16 data;
                    vci((vector_s16&)idx,(int16_t)0,INC_ORDER);
                    vmuls(idx,idx,(uint16_t){stride},p); {mem}
                }}''')
    cases['extract_fp32'] = kernel('''__VEC_SCOPE__ {
        vector_bool p=pset_b32(PAT_ALL); vector_f32 x,y; vector_align u;
        sprclr(std::integral_constant<Spr, static_cast<Spr>(74)>());
        for(uint16_t i=0;i<8;++i) {
            vlds(x,src,i*64,NORM); vsqz(y,x,p,MODE_STORED); vstur(u,y,dst,POST_UPDATE);
        }
        vstar(u,dst);
    }''',dtype='float')
    for odd in (0,1):
        lhs,rhs=('none','all') if odd else ('all','none')
        cases[f'extract_topk_{odd}'] = kernel(f'''__VEC_SCOPE__ {{
            vector_bool all=pset_b32(PAT_ALL), none=pset_b32(PAT_ALLF), pattern, unused;
            vector_bool load=pset_b32(PAT_VL32), execute;
            vector_f32 x,y; vector_align u;
            sprclr(std::integral_constant<Spr, static_cast<Spr>(74)>());
            pintlv_b32(pattern,unused,{lhs},{rhs});
            for(uint16_t i=0;i<8;++i) {{
                vlds(x,src,i*64,NORM); pand(execute,pattern,load,load);
                vsqz(y,x,execute,MODE_STORED); vstur(u,y,dst,POST_UPDATE);
            }}
            vstar(u,dst);
        }}''',dtype='float')
    for n in (1,2,8,32):
        cases[f'bitsort_r{n}'] = kernel(f'vbitsort(dst,src,idx,{n});',
            prelude='__ubuf__ uint32_t *idx=(__ubuf__ uint32_t*)get_imm(0x9000); for(int i=0;i<1024;++i) idx[i]=i; set_flag(PIPE_S,PIPE_V,(event_t)0); wait_flag(PIPE_S,PIPE_V,(event_t)0);',dtype='float')
    for name, count, repeats, lists in [('32_r8',32,8,4),('128_r2',128,2,4),('10_r1',10,1,2)]:
        count_word=sum(count << (16*i) for i in range(lists))
        config=repeats|(((1<<lists)-1)<<8)
        pitch=1024 if lists==2 else count*2
        init=f'''set_flag(PIPE_MTE2,PIPE_S,(event_t)1); wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
        for(int r=0;r<{repeats};++r) for(int l=0;l<{lists};++l) for(int i=0;i<{count};++i) {{
            int rank=r*{count*lists}+i*{lists}+l;
            int offset=r*{count*lists*2}+l*{pitch}+i*2;
            src[offset]=(float)(4096-rank); ((__ubuf__ uint32_t*)src)[offset+1]=rank;
        }}
        set_flag(PIPE_S,PIPE_V,(event_t)0); wait_flag(PIPE_S,PIPE_V,(event_t)0);'''
        cases[f'mrgsort_fenced_{name}'] = kernel(f'''__ubuf__ float *addr[4]={{src,src+{pitch},src+{pitch*2},src+{pitch*3}}};
            vmrgsort4(dst,addr,{count_word}ULL,{config}ULL);''',prelude=init,dtype='float')
    for name, source in cases.items():
        (out/f'{name}.cce').write_text(source,encoding='utf-8',newline='\n')
    print(f'Generated {len(cases)} probes in {out}')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('out',type=Path); generate(p.parse_args().out)
