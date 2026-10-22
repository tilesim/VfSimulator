# VFtest 算子 II 与 forwarding 覆盖

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`。
范围为 `VFtest/cce/`、`VFtest/canonical/`、`VFtest/json/` 中的完整算子和依赖探针。
`contracts`、`ordinary_instructions`、`fixtures`、`cce_expected`、专门的 `membar_timing` 在本次范围外。

## 覆盖范围

| 项目 | 数量 |
| --- | ---: |
| 输入文件 / 去重语义程序 | 74 / 56 |
| COMPUTE opcode/form | 25 |
| 各输入有向计算矩阵的并集 | 325 / 325 组 II |
| 展开后实际寄存器、谓词 RAW | 103 / 103 组 forwarding |
| RAW 精确 pair / 已标定的 PSET producer 默认规则 | 74 / 29 |
| 新增实测 II / forwarding | 162 / 10 |
| 本轮有效数值案例 | 686 |

II 矩阵按各输入的 COMPUTE form 集合生成，保留共享 EXU 端口的有向组合，再取并集。
forwarding 按 definition_id 和完整迭代路径追踪动态 RAW，包含循环携带值。
25 个 form 的完整全局笛卡尔积为 625；本次 325 是各输入内实际 form 矩阵的并集。
本范围没有缺失参数回退。29 组 PSET RAW 使用已有、经过标定的 producer 默认规则。
完整跨 opcode/dtype 笛卡尔积保留独立测量范围。

输入覆盖 GeLU、GeLU_poly、SiLU、SwiGLU、在线更新、Softmax、向量长链及依赖探针，
包含 FP16/FP32 混合计算、FP32→FP16 转换、谓词、归约和广播、pack 与 BF16 块 STORE。
逐文件 form、II 矩阵、RAW、参数解析路径与源文件哈希见 [机器清单](operator_pairs.json)。

## 新增 forwarding

单位为 cycle；二元 consumer 分别测试 Vn、Vm，STORE 测试 Vd。

| producer | consumer | forwarding |
| --- | --- | ---: |
| `VCADD.fp32` | `VDUP.fp32` | 19 |
| `VDUPS.fp32` | `VADD.fp32` | 2 |
| `VDUPS.fp32` | `VMAX.fp32` | 2 |
| `VEXP.fp32` | `VCADD.fp32` | 13 |
| `VEXP.fp32` | `VMULSCVT.f32_to_f16` | 13 |
| `VEXPDIF.fp32` | `VCVT_F32_TO_F16.f32_to_f16` | 15 |
| `VLD.fp32` | `VMULS.fp32` | 6 |
| `VLDS.fp32` | `VCMP_GT.fp32` | 6 |
| `VPACK.u32_to_u16` | `VSSTB.bf16` | 9 |
| `VSEL.fp32` | `VSTS.fp32` | 4 |

## II 与证据

新增 II 中 154 组为 1 cycle、5 组为 2 cycle、1 组为 4 cycle、2 组为 5 cycle。
例如 `VCADD.fp32→VCMAX.fp32=4`，`VCADD.fp32→VADD.fp32/VMAX.fp32=5`。
同一指令对按有方向的 opcode/form 计数；两个可用端口各至少两次重现同一最小值。
单端口归约按其实际端口检查，孤立对照排除在途容量与其他指令干扰。

每个样本先检查数值、保护区和目标 RV 数量，再检查同端口实际相邻发射、真实 RAW、
ISU 源就绪、IDU 分发、self-II 历史及其他在途指令的完成冲突。
相邻两条自身的完成冲突参与有效转换间隔，证据中单独标记；该间隔不能单独推导底层资源结构。
LOAD→计算还要求 popped 等于 LSU I1，排除访存排队。

690 个有输出的实验案例中，686 个通过数值与目标计数检查；初始模板的 4 个数值失败案例
保留诊断并排除。编译失败、超时和未隔离的 LOAD 观测同样不参与参数录入。
通过隔离的测量确认 164 组 II，其中 2 组与已有精确参数相同，新增 162 组。

测量方法、源位置、特殊输入及复现命令见
[benchmark skill](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/operator-pairs.md)，
逐事件与文件 SHA256 见 [精简证据](https://github.com/tilesim/VfSimulator/blob/4cc20155f36be92e24772e4909bd7b3148c6e389/skills/vfsim-instruction-benchmark/references/evidence/a5-operator-pairs.json)。
原始源码、host、二进制、输入、golden、输出及日志位于
`results/operator_pairs_20261020/raw_runs.tar.gz`。

## 回归精度

验证保持每 EXU inflight=7、opcode/form self-II 历史及每 EXU 每周期一条完成。
API、Core 与 Native 仿真实现保持固定；Python/C++ 共用新增参数。
444 个 Python 测试、8 个 CTest 通过。两处参数测试使用实测的 `VADD→PSET II=1`。
37 组固定 Canonical 输入逐例核对 Python/C++ 周期与发射、完成日志，全部一致，参数缺失 warning 为 0。
其中 33 组有 CAModel 参考周期，另 4 组仅验证执行及双实现一致性。

| 集合 | 有参考案例 | 补参前 MAPE | 补参后 MAPE | 补参前 WAPE | 补参后 WAPE |
| --- | ---: | ---: | ---: | ---: | ---: |
| 原回归 | 21 | 4.632% | 4.632% | 3.575% | 3.575% |
| Softmax | 12 | 3.277% | 3.394% | 3.211% | 3.351% |
| 合并 | 33 | 4.139% | 4.182% | 3.562% | 3.567% |

原有 25 个回归案例的预测周期全部保持一致。Softmax 中 4 个案例误差减小、6 个增大、2 个保持一致，
覆盖补齐与全算子精度分别评估。固定时序参数仍不能完整描述所有资源竞争与调度行为。
历史基线文件保持固定，其精度门限有 3 项既存超差，补参前后失败列表相同。

| Softmax 用例 | 参考 | 补参前 | 补参后 | 相对绝对误差 |
| --- | ---: | ---: | ---: | ---: |
| expdif_mulcvt_u1 | 713 | 685 | 672 | 5.750% |
| expdif_mulcvt_u2 | 609 | 633 | 636 | 4.433% |
| expdif_mulcvt_u4 | 594 | 633 | 635 | 6.902% |
| expdif_vcvt_u1 | 615 | 672 | 658 | 6.992% |
| expdif_vcvt_u2 | 622 | 621 | 622 | 0.000% |
| expdif_vcvt_u4 | 618 | 628 | 630 | 1.942% |
| sub_exp_mulcvt_u1 | 701 | 714 | 714 | 1.854% |
| sub_exp_mulcvt_u2 | 668 | 695 | 692 | 3.593% |
| sub_exp_mulcvt_u4 | 660 | 697 | 697 | 5.606% |
| sub_exp_vcvt_u1 | 686 | 697 | 699 | 1.895% |
| sub_exp_vcvt_u2 | 681 | 684 | 681 | 0.000% |
| sub_exp_vcvt_u4 | 682 | 684 | 694 | 1.760% |

完整指标、输入与时序哈希、历史基线对照保存在 `results/operator_pairs_20261020/summary.json`，
执行入口为同目录的 `run_regression.py`。对照使用固定输入及相同模型源码与配置，仅更改 II/forwarding 表。
