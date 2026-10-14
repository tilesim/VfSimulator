"""生成并运行 POST_UPDATE barrier 积压对照实验。"""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/post_update_backlog_probe'
RUN = Path('/tmp/vfsim_post_update_backlog_probe')
TOOLKIT = Path('/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1')


def source(mode, name):
    aliases = '\n'.join(f'__ubuf__ float *p{j}=p+{j*64};' for j in range(8)) if mode == 'independent' else ''
    loads = []
    for j in range(8):
        if mode == 'same':
            args = 'p,64,NORM,POST_UPDATE'
        elif mode == 'independent':
            args = f'p{j},512,NORM,POST_UPDATE'
        else:
            args = f'p,512*i+{j*64},NORM'
        loads.append(f'vlds(a{j},{args});')
    stores = '\n'.join(f'vsts(a{j},out,512*i+{j*64},NORM_B32,mask);' for j in range(8))
    chain = '\n'.join('vadds(x,x,1.0f,mask);' for _ in range(24))
    return f'''#ifdef __CCE_KT_TEST__
#define __aicore__
#else
#define __aicore__ [aicore]
#endif
__attribute__((always_inline)) inline [aicore] void {name}_vf(
    __ubuf__ float *p, __ubuf__ float *scratch, __ubuf__ float *out) {{
    {aliases}
    __VEC_SCOPE__ {{
        vector_bool mask=pset_b32(PAT_ALL);
        vector_f32 x, {', '.join(f'a{j}' for j in range(8))};
        vlds(x,p,0,NORM);
        {chain}
        vsts(x,scratch,0,NORM_B32,mask);
        mem_bar(VST_VLD);
        for(uint16_t i=0;i<2;++i) {{
            {chr(10).join(loads)}
            {stores}
        }}
    }}
}}
extern "C" __global__ __aicore__ void {name}(
    __gm__ float *__restrict__ input0, __gm__ float *__restrict__ output0) {{
    __ubuf__ float *p=(__ubuf__ float *)get_imm(0x00000);
    __ubuf__ float *scratch=(__ubuf__ float *)get_imm(0x04000);
    __ubuf__ float *out=(__ubuf__ float *)get_imm(0x08000);
    copy_gm_to_ubuf_align_v2(p,input0,0,1,4096,0,0,0,0,0,0);
    set_flag(PIPE_MTE2,PIPE_V,(event_t)0);
    wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
    {name}_vf(p,scratch,out);
    set_flag(PIPE_V,PIPE_MTE3,(event_t)0);
    wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
    copy_ubuf_to_gm_align_v2(output0,out,0,1,4096,0,0,0);
}}
'''


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ACL_PATH=str(TOOLKIT),
               CCEC_EXTRA_FLAGS='-mllvm -cce-aicore-vec-misched=0')
    libs = [TOOLKIT / p for p in (
        'aarch64-linux/simulator/Ascend950PR_9599/lib',
        'simulator/Ascend950PR_9599/lib', 'tools/simulator/Ascend950PR_9599/lib',
        'lib64', 'x86_64-linux/devlib', 'x86_64-linux/devlib/device',
        'x86_64-linux/lib64/device/lib64')]
    env.update(ASCEND_TOOLKIT_HOME=str(TOOLKIT), NPU_TYPE='Ascend950PR_9599',
               LD_LIBRARY_PATH=':'.join(map(str, libs)) + ':' + env.get('LD_LIBRARY_PATH', ''))
    for mode in ('same', 'independent', 'explicit'):
        name = f'post_update_backlog_{mode}'
        path = OUT / f'{name}.cce'
        path.write_text(source(mode, name))
        with (OUT / f'{mode}_build.log').open('w') as log:
            subprocess.run(['bash', str(ROOT / 'ascend_runner/current/build_native_simexec.sh'),
                            str(path), name], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        build = ROOT / 'ascend_runner/build' / f'{name}_native_simexec'
        run = RUN / mode
        run.mkdir(parents=True, exist_ok=True)
        app = run / f'{name}_simexec'
        shutil.copy2(build / app.name, app)
        binary = run / f'{name}_mix.o'
        shutil.copy2(build / binary.name, binary)
        with (OUT / f'{mode}_run.log').open('w') as log:
            subprocess.run([str(app), str(binary), name, '1', '1', '1024'],
                           cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=180)
        expected = (run / 'input0.bin').read_bytes()
        actual = (run / 'output0.bin').read_bytes()
        if len(actual) != 4096 or actual != expected:
            raise AssertionError(f'{mode}: output differs from input')
        print(f'{mode}: output matches input (1024 fp32); logs: {run}', flush=True)


if __name__ == '__main__':
    main()
