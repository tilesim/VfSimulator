# 指令时序与测量证据

Catalog 声明签名、form 和地址语义，`isa.json`、`forwarding.json`、
`InitiationInterval.json` 保存时序参数。目标为 CANN `9.0.0-beta.1`、
`dav-c310-vec`、`Ascend950PR_9599`。完整类型清单见 [A5 覆盖清单](a5_instruction_coverage.md)，
数值实验、参数来源与限制见 [覆盖进度](instruction_coverage/a5/progress.md)。

## 测量口径

计算 latency 使用同一动态指令的 EXQ issue/popped 到完成的差值；
普通访存使用同 ID 的 LSU I1 到完成差值，并保留 popped 到完成的排队观测。
GSU 指令分别记录孤立请求和连续请求。EXU PERF 起点通常比 popped 晚一周期，
II 分析统一周期口径，并核对同端口、源已就绪的独立指令。

forwarding 使用真实 producer→consumer 的最早发射间隔，核对具体结果、源位置、
共同谓词及 IDU 源依赖。多结果指令分别检查每个结果，单值参数取所需间隔的最大值。
同 opcode/form 没有合法 RAW 的指令不要求 self-forwarding。

探针保存源码、输入、独立 golden、输出、完整命令及动态日志。
数值失败、编译失败和运行超时的样本保留诊断，不用于写入实测参数。
[标定 skill 的 evidence 目录](https://github.com/tilesim/VfSimulator/tree/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/evidence/) 下的机器证据保留逐事件数据和配置变化，原始运行产物放在 Git 忽略的 `results/`。

## 计算与谓词

| form / 类别 | latency | self-II | 说明 |
| --- | ---: | ---: | --- |
| VDUP，向量源广播 | 11 | 1 | EXU0_ONLY，self-forwarding 8 |
| VDUPS，标量源广播 | 6 | 1 | 双端口，没有同形输出→标量 RAW |
| VBR | 6 | 1 | 无谓词标量广播；双向量形式按软件规则展开 |
| VCI | 7 | 2 | 原生 8/16/32-bit 类型；双向量形式展开 |
| VMOV | 6 | 1 | 普通寄存器复制，self-forwarding 2 |
| MOVVP.uint16 / uint32 | 16 | 4 | EXU0_ONLY；part 选择 16B/8B 源块，逐 bit 扩展至谓词 |
| PSET_B8/B16/B32 | 6 | 1 | 独立发射样本；PGE 使用相同编码 |
| PLT/PLTM.b8/b16/b32 | 6 | 2 | 标量计数产生谓词；64-bit 双向量接口使用 B32 分组 |
| VEXPDIF.fp16 / fp32 | 19 / 18 | 4 | form 由源类型选择，结果 FP32；self-forwarding 16 / 15 |
| DHISTV2/CHISTV2.uint16 | 15 | 4 | EXU0_ONLY，旧目的值与显式源 RAW 均为 12 |
| VINTLV/VDINTLV，原生 dtype | 11 | 2 | EXU0_ONLY；两个结果的 forwarding 分别为 8 / 9 |
| VSQRT.fp32 / VLN.fp32 | 17 / 18 | 4 | SFU，双端口 |

`VMULS.bf16` 展开为 VDUPS 和 VMUL。masked MODE_MERGING 操作显式保留旧目的依赖；
需要时生成 VMOV_MERGE。16-bit VDUPI 生成低部和高部两条指令，二者的零间隔依赖
表达值拼接顺序。该零值不作为硬件 RAW forwarding 的测量结论。

普通计算 295 个类型组合、归约和多结果 84 个组合、转换和 pack/unpack 的测量
见 [覆盖进度](instruction_coverage/a5/progress.md)。转换以源/目的类型对查询物理 form，
显式寄存器视图保留底层 value 身份。

常用跨 opcode 组合分别测量 FP32、FP16、BF16：forwarding 覆盖计算 RAW、
LOAD→计算、计算→STORE 和 compare→select；II 按独立同端口流测量两个方向。
例如 VADD→VMUL 的 II 为 1，VMUL→VADD 为 2。来源、逐对数值及隔离条件见
[常用指令对测量](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/common-instruction-pairs.md)。
完整算子与依赖探针的需求按输入内 COMPUTE 矩阵和动态 RAW 统计，详见
[VFtest 算子指令对](instruction_coverage/a5/operator_pairs.md)。
Membar 归档的 A5 算子覆盖 992 组 II 和 325 组静态 RAW 查询，
范围、同编码继承和逐版本输入状态见 [归档算子指令对](instruction_coverage/a5/membar_pairs.md)。
完整跨 opcode/dtype 笛卡尔积尚未逐对测量，精确配置缺失时 ParamDB 保留回退及 warning。

EXU 按端口和 opcode/form 保存 self-II 的最早允许发射周期。EXQ 实际发射同时检查
该周期与同端口最近已发射指令的转换限制：

```text
earliest(A) = max(last_issue(A) + II(A,A), last_issue(B) + II(B,A))
```

其中 B 为同端口最近实际发射的指令。每项使用对应前序指令的发射时刻；
其他 opcode 的发射保留 A 已有的 self-II 限制。操作数就绪、接收延迟、
端口每周期接收一条及执行容量分别约束发射。直接发射模式遵循所选跨 FU 策略，
并检查相同的 opcode/form self-II。

EXQ 预测还检查已发射的 self-II 截止周期、队尾预测转换间隔和队列中最近同
opcode/form 的预测发射间隔。预测用于端口选择，实际发射通过 EXU 再判断当前状态。
回归范围与误差见
[II 发射规则验证](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/docs/validation/issue_interval_regression_20261020.md)。

每个 EXU 每周期最多完成一条计算指令。EXU 发射时预留 `issue_cycle + latency`
对应的完成周期；若该周期已被同一 EXU 的在途指令占用，候选指令推迟发射，
直到找到空闲完成周期。连续冲突可以推迟多个周期，指令本身的 latency 保持不变。
例如 latency=16 的指令在 100 发射并预留 116；latency=7 的后续指令在 109
发射会发生冲突，因此最早在 110 发射、117 完成。

完成带宽在同一 EXU 的 ALU、SFU 和谓词计算之间共享，两个 EXU 分别维护预留状态；
LSU 继续维护独立的访存完成规则。EXQ 预测读取已发射指令的预留状态，实际发射
再次检查并预留。该规则支持多条指令重叠执行，并与 self-II、转换 II、每周期
发射带宽及 inflight 上限共同约束调度。默认 inflight 上限为每 EXU 7 条，输入覆盖
值 0 表示无限制。四组对照与模型适用范围见
[EXU 完成带宽验证](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/docs/validation/exu_completion_bandwidth_20261020.md)。
更复杂的共享资源占用仍需单独标定。

FP32 VCMAX 的 self-II=1、仅使用 EXU0；它与 VEXP 的双向转换可达到 1，
VCMAX→VADD 的转换观测为 3。VADD/VMUL 和 VEXP/VDIV 的交错实验表明，
self-II、端口单周期发射限制和共享资源冲突需要分别考虑。
[FP32 执行资源实验](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/fp32-fu-contention.md)
记录源程序与实际发射流、方向间隔、ISU 阻塞原因和资源分组建议。

## 访存与状态

| 类别 | 配置与范围 |
| --- | --- |
| 普通对齐/非对齐访存 | 有效样本通常为 LSU latency 9，完整 dtype 列在机器清单 |
| ONEPT_B8/B16 STORE | latency 13；ONEPT_B32 为 9 |
| 双结果 INTLV_B8/B16/B32 STORE | latency 11，512B 传输 |
| 分布模式 | VLDS/VLD 共 21 种，VSTS/VST 共 13 种；跨度及 UB 传输量按模式定义 |
| 谓词 NORM / US / DS / PK | 分别传输 32 / 16 / 64 / 16B，地址单位为字节 |
| PLDI/PSTI 的立即数 | NORM/DS 单位 32B，US/PK 单位 16B；PLDS/PSTS 参数单位为字节 |
| GSU gather/scatter | latency 与服务间隔对应已测索引分布，运行时 bank 冲突需要另行校准 |
| VSSTB | 原生类型 latency 10，已测块步幅 2 的服务间隔为 2；其他步幅保留适用范围 |
| align state | 初始化、捕获、append、consume 显式串联，状态发布与向量数据完成独立建模 |

分布模式使用 `memory_mode_latency` 选择时序，模式同时参与 Profile 缓存键。
VAG 的一至四层步幅形成字节地址表达式。LOAD 等待其 index 向量和谓词；
LSU 的共享 GSU 服务约束与普通 UB 带宽预算分别维护。

字节辅助指令用于双向量 vload/vstore 和 gather/scatter 展开。
VSTUS_BYTES/VSTAS_BYTES 使用完整向量 latency 9；部分或零计数尾块观察到 8，
完整向量配置对这部分尾块可能多估一周期。

## 同步

VST_VLD、VLD_VST、VST_VST、VV_ALL 分别约束相应的先前与后续 LOAD/STORE。
VV_ALL 只阻止对应访存跨越屏障，计算指令继续遵循普通 RAW。
VST_VLD/VLD_VST 使用已校准时序；VST_VST/VV_ALL 当前按依赖等待工作，
独立屏障开销未测时发出 `unmeasured_membar_timing`。

## 工具链与模型边界

41 个类型 form 缺少有效 latency：PSLIDE 的三个分组被目标编译器拒绝；
VSELRV2 的四种类型及四种 FP16/BF16→FP8 转换缺少 CAModel VALU 条目；
VSTUR/VSTAR 的 30 个类型 form 未取得可完成的 CAModel 运行。
相应 COMPUTE form 的 11 项 self-II 保留未测状态。

VADDCS/VSUBCS 的 S32/U32 旧 Pd carry-in 未出现在 CAModel IDU 源清单中，
四条完整 self-forwarding 路径保留为未确认。VDUPI 高部的端口资格仍使用明确标注的近似。
CANN wrapper 的定义域、数值差异与非有限低精度输入范围见覆盖进度。
