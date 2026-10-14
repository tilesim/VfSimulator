# 指令时序与测量证据

指令签名和形式由 Catalog 声明，latency、执行单元、forwarding 和 II 由参数表维护。
本文汇总本仓库保存的测量结论和近似范围；实际数值以 `configs/` 为准。

## 测量口径

测量样本主要来自 CANN 9.0.0-beta.1、dav-c310-vec、Ascend950PR_9599 的 CAModel。
latency 使用同一动态指令的 popped/EXQ issue 到完成的差值。
EXU PERF 的执行起点可能比 popped 晚 1 cycle，分析时需统一起点。
forwarding 使用 producer 到 consumer 的发射间隔，需排除其他源等待和端口竞争。
self-II 使用同端口独立指令样本，并核对编译后动态指令数量。

探针同时保存源码、编译命令、golden、输出和按动态 ID 关联的日志。
数值正确性采用独立 golden 校验，时序使用对应指令事件。
`results/` 和临时运行目录由 Git 忽略；探针源码、测试 fixture 和配置参与版本管理。

## 计算指令配置

| opcode/form | latency | FU / 端口 | 测量覆盖或近似 |
| --- | ---: | --- | --- |
| VSQRT.fp32 | 17 | SFU / EXU01 | FP32 样本，端口有日志依据 |
| VLN.fp32 | 18 | SFU / EXU01 | FP32 样本，端口有日志依据 |
| VOR.b32 | 6 | ALU / EXU01 | 通过校验的 B32 样本，ALU 为功能分类 |
| VCI.int32 | 7 | ALU / EXU01 | S32 索引样本，ALU 为功能分类 |
| VCVT_BF16_TO_F32.bf16_to_f32 | 7 | ALU / EXU01 | 对应转换形式的样本 |
| VCVT_S32_TO_U8.s32_to_u8 | 7 | ALU / EXU01 | RS_ENABLE 等已测模式 |
| VDUP.int8、VBR.int8 | 6 | ALU / EXU01 | 窄类型样本；其他时序对需单独验证 |
| VMOV.fp32 / b32 | 6 | ALU / EXU01 | 显式寄存器复制，两个端口均有样本 |
| VINTLV、VDINTLV.fp32 / b32 | 11 | ALU / EXU0_ONLY | 双输入、双输出；端口采用 EXU0 保守配置 |
| VCMP、VCMPS 的 EQ/NE/GT/GE/LT/LE.fp32 | 6 | ALU / EXU01 | 全有效 mask、有限 FP32 样本 |
| VSEL.fp32 | 6 | ALU / EXU01 | 比较探针中的消费样本 |
| PAND.b8、POR.b8 | 7 | ALU / EXU01 | 谓词逻辑样本，双端口为模型放置近似 |
| MOVVP.uint16 / uint32 / b16 / b32 | 16 | ALU / EXU01 | B16/B32 的 part=0、全零/全一样本 |
| PSET_B32.b32 | 6 | ALU / EXU01 | B32 latency 和部分 consumer forwarding 已测 |
| PSET_B16.b16、PSET_B8.b8 | 6 | ALU / EXU01 | 沿用 B32 时序近似 |
| PLT.b32 | 6 | ALU / EXU01 | 尾块样本，端口放置为谓词计算近似 |

VOR 的 form 固定为 b32，值 dtype 为 uint32。VCI 的 CCE 参数为 destination、scalar
index 和可选 INC_ORDER。VCVT 依据源/目的 dtype 选择 specialization 和签名。
MOVVP 的 part 约束由 form 决定，B16 为 0..15，B32 为 0..31。

VMOV 表达实际向量复制。CCE `dst=src` alias 通过值关系处理。
VINTLV/VDINTLV 的两个结果分别参与定义和资源分配，共享 Uop 的执行时间。

## 已配置的 forwarding 和 II

| producer → consumer | forwarding | 依据或范围 |
| --- | ---: | --- |
| VLDS.fp32 → VSQRT.fp32 / VLN.fp32 | 6 | 对应 FP32 样本 |
| VSQRT.fp32 → VSQRT.fp32 / VSTS.fp32 | 14 / 15 | 对应 FP32 样本 |
| VLN.fp32 → VLN.fp32 / VSTS.fp32 | 15 / 16 | 对应 FP32 样本 |
| VLDS.uint32 → VOR.b32 | 6 | B32 数值通过的样本 |
| VOR.b32 → VSTS.uint32 | 4 | 同样本的结果写回 |
| VCI.int32 → VSTS.int32 | 5 | S32 索引样本 |
| VDUP.fp32 → VMOV.fp32 / b32 | 2 | 显式复制探针 |
| VLDS.fp32 → VMOV.fp32 / b32 | 6 | 普通 NORM 加载探针 |
| VMOV.fp32 / b32 → VAXPY.fp32 | 2 | A5 显式复制消费样本 |
| VMOV → VMOV / VSTS | 2 / 4 | 复制依赖链和末端 store |
| VINTLV、VDINTLV → VSTS | 9 | 模型统一值；第二输出样本观测为 10 |
| PSET_B* → `*` | 2 | B32→VDUP/VADD 已隔离，其他位宽和 consumer 使用近似 |
| VLDAS → VLDUS | 1 | 加载侧内部等待的模型近似 |
| PSTU → VSTAS | 1 | 状态链与 flush 样本 |

VSQRT.fp32、VLN.fp32 的 self-II 为 4；VCI.int32 为 2；VMOV 为 1。
VOR 的独立 self-II 和窄类型指令的完整时序对仍缺少独立测量。
PSET 的缺失 II 使用 ParamDB 的 latency 差值近似：前条比后条大 1 时取 2，其他情况取 1。
该规则描述相邻发射冲突，任意在途指令的写回预约超出其建模范围。

## PSET 隔离样本

向量源已经就绪的样本可用于检查 mask forwarding。

| 探针 | 填充组数 G | PSET issue | consumer issue | 间隔 | VF cycles |
| --- | ---: | ---: | ---: | ---: | ---: |
| VDUP，无 VLDS | 0 | 22 | 24 | 2 | 49 |
| VADD，PAT_VL32 | 0 | 22 | 27 | 5 | 53 |
| VADD，PAT_VL32 | 4 | 24 | 29 | 5 | 57 |
| VADD，PAT_VL32 | 8 | 25 | 31 | 6 | 61 |
| VADD，PAT_VL32 | 12 | 33 | 35 | 2 | 65 |
| VADD，PAT_VL32 | 16 | 55 | 57 | 2 | 83 |
| VADD，PAT_ALL | 12 | 33 | 35 | 2 | 65 |

G=0/4/8 的样本包含向量源等待或队列竞争；G=12/16 支持 2-cycle 的有效 mask 间隔。
SHQ ready 在 producer issue 后 1 cycle，EXQ 转移再占 1 cycle。
启动探针 `pset_vadd_i16` 的 CAModel VF 时间为 72，PSET issue/done 为相对 22/28，
首条 VADD issue 为 31。该 workload 同时受加载和循环控制影响，适合检查整体阶段时序。

## 搬运与打包样本

| 样本 | 观测 | 模型表达及范围 |
| --- | --- | --- |
| VLDS DINTLV_B32，FP32 offset=0/8 | 单条 RV_VLDI、两个结果、latency=9，VF=51 | 双结果 LOAD，span=128，512 B 预算 |
| 普通 NORM 双加载 / DINTLV 双加载，8 轮 | VF=75/104；同周期双 LOAD 的周期数为 7/0 | DINTLV 使用 512 B 共享预算，IDU UOP-Split 配额未建模 |
| VLDAS/VLDUS，FP32/FP16/S32 连续访问 | 各条 latency 常见 9，payload 连续性通过 | 状态初始化和捕获，latency 配置为 9 |
| VLDAS/VLDUS 无指针更新 | 同周期发射，UB 请求相隔 1，VLDUS issue-to-done=10 | 状态 ready=start+1、latency=9 的内部等待近似 |
| PSTU B32/B16 与 VSTAS | PSTU 出现 8/9，flush 可在末条 start 后 1 cycle 发射 | PSTU 固定 latency=8，状态 forwarding=1 |
| FP16 UNPK_B16 + PLT + PK_B32，37/64 元素 | VF=50；37 元素写回 74 B，64 元素写回 128 B | span=64，部分 mask 保持 256 B 事务预算 |

PSTU 在 B32/B16 下分别打包 8/16 B，探针对整个哨兵区执行字节校验。
DINTLV 的双发实验观察到 IDU `UOP-Split number reached`；不同 workload 的总周期差
同时包含字节数、计算和存储数量变化。混发规则及各模式的完整吞吐仍需独立探针验证。

## 探针和测试

完整检出中的 `cce_code/predicate_select_test/` 提供生成、运行、golden 校验和归档脚本，
运行环境需要对应 CANN 编译器和 CAModel。

```bash
python3 cce_code/predicate_select_test/run.py --probe vmov_chain
python3 cce_code/predicate_select_test/run.py --probe vmov_independent
python3 cce_code/predicate_select_test/run.py --probe pset_vadd_gap --gap 12
python3 cce_code/predicate_select_test/run.py --probe vintlv --reverse-stores
python3 cce_code/predicate_select_test/compute_sweep.py --out /tmp/predicate-compute-probe
python3 cce_code/predicate_select_test/memory_probe.py --kind vldus_no_update --dtype fp32 --offset 1 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind pstu --offset 0 --iterations 5
python3 cce_code/predicate_select_test/memory_probe.py --kind unpack_pack_tail --dtype fp16 --offset 0 --iterations 1 --active-elements 37
```

相关模型测试为 `test_ordinary_fp32_onboarding`、`test_ordinary_bf16_onboarding`、
`test_ordinary_narrow_onboarding`、`test_ordinary_vor_vci_onboarding`、`test_predicate_logic`、
`test_interleave`、`test_vmov`、`test_vldas_vldus`、`test_pstu` 和 `test_packed_memory_modes`。
各测试分别核对语义、参数和周期，Native runner 可用于双实现对比。
