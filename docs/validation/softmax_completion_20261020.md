# Softmax 完成带宽与 inflight 验证

验证日期为 2026-10-20，分支为 `dev_wch/refactor`。输入来自
`VFtest/cce/softmax/` 的全部 12 个 CCE 文件：四种指令组合，各包含 U1、U2、U4
三个循环展开版本。所有用例使用 `kRows=128`、`kCols=64`。

参考周期逐例取自 `regression_suite/evidence/cce/softmax/` 中对应的
`core0.veccore0.instr_log.dump`，字段为 `vf_execute_time`。
这些日志保存了 CAModel 的测量结果；本轮运行对象为微架构预测模型。

本报告使用实验保存的固定时序参数快照。算子参数覆盖与相应回归见
[VFtest 算子指令对](../instruction_coverage/a5/operator_pairs.md)。

## 固定参数快照的结果

默认设置为每 EXU inflight 上限 7、每周期最多完成一条计算指令。
下面的预测周期包含 VF drain，误差为 `abs(prediction-reference)/reference`。

| 指令组合 | 展开 | CAModel 周期 | Python/C++ 预测周期 | 相对绝对误差 |
| --- | --- | ---: | ---: | ---: |
| `expdif_mulcvt` | U1 | 713 | 685 | 3.93% |
| `expdif_mulcvt` | U2 | 609 | 633 | 3.94% |
| `expdif_mulcvt` | U4 | 594 | 633 | 6.57% |
| `expdif_vcvt` | U1 | 615 | 672 | 9.27% |
| `expdif_vcvt` | U2 | 622 | 621 | 0.16% |
| `expdif_vcvt` | U4 | 618 | 628 | 1.62% |
| `sub_exp_mulcvt` | U1 | 701 | 714 | 1.85% |
| `sub_exp_mulcvt` | U2 | 668 | 695 | 4.04% |
| `sub_exp_mulcvt` | U4 | 660 | 697 | 5.61% |
| `sub_exp_vcvt` | U1 | 686 | 697 | 1.60% |
| `sub_exp_vcvt` | U2 | 681 | 684 | 0.44% |
| `sub_exp_vcvt` | U4 | 682 | 684 | 0.29% |

12 个用例全部完成仿真，MAPE 为 **3.277%**，WAPE 为 **3.211%**，
按 `1-MAPE` 计算的平均预测精度为 **96.723%**。
最大相对绝对误差为 `expdif_vcvt_u1` 的 **9.268%**。

## 完成带宽与 inflight 的四组对照

固定 CCE 输入、循环参数、ISA、forwarding、II 及全部基础配置。
每个输入只覆盖 `exq_issue_inflight_cap_per_port` 为 0 或 7，0 表示无限制。
两种完成带宽分别使用上一轮保存的 EXU 实现和当前实现，二者均按 opcode/form
保存 self-II 限制，并检查相邻指令转换 II。实现与二进制 SHA256 记录在实验目录。

| 每 EXU inflight 上限 | 完成带宽无约束 MAPE | 每周期一条完成 MAPE | 无约束 WAPE | 每周期一条 WAPE |
| --- | ---: | ---: | ---: | ---: |
| 无限制，输入值 0 | 11.141% | 4.517% | 11.161% | 4.510% |
| 7 | 2.805% | 3.277% | 2.790% | 3.211% |

无限制 inflight 下，完成带宽约束使 11 个用例误差减小、1 个增大；
单端口峰值 inflight 从 12 降至 10。
inflight=7 下，5 个用例误差减小、7 个增大，MAPE 增加 0.472 个百分点。
四组设置中，每个用例的预测周期都随完成带宽设置发生变化。

`expdif_vcvt_u1` 在 inflight=7 下从 640 增至 672 cycle，参考为 615，
误差从 4.07% 增至 9.27%；无限制 inflight 下从 587 增至 647，
误差从 4.55% 增至 5.20%。该案例在两组 inflight 设置下均需要进一步校准。

| 用例 | 参考周期 | cap=0 无约束 | cap=0 一条 | cap=7 无约束 | cap=7 一条 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `expdif_mulcvt_u1` | 713 | 603 | 631 | 655 | 685 |
| `expdif_mulcvt_u2` | 609 | 528 | 562 | 627 | 633 |
| `expdif_mulcvt_u4` | 594 | 543 | 590 | 631 | 633 |
| `expdif_vcvt_u1` | 615 | 587 | 647 | 640 | 672 |
| `expdif_vcvt_u2` | 622 | 522 | 569 | 618 | 621 |
| `expdif_vcvt_u4` | 618 | 530 | 580 | 630 | 628 |
| `sub_exp_mulcvt_u1` | 701 | 650 | 695 | 703 | 714 |
| `sub_exp_mulcvt_u2` | 668 | 595 | 656 | 685 | 695 |
| `sub_exp_mulcvt_u4` | 660 | 611 | 650 | 684 | 697 |
| `sub_exp_vcvt_u1` | 686 | 605 | 681 | 677 | 697 |
| `sub_exp_vcvt_u2` | 681 | 584 | 644 | 674 | 684 |
| `sub_exp_vcvt_u4` | 682 | 615 | 654 | 676 | 684 |

## 合并回归结果

将本组 12 个参考案例与
[完成带宽验证](exu_completion_bandwidth_20261020.md) 的 21 个参考案例合并，
得到 33 个参考案例。原回归集中另外 4 个缺少参考周期的案例独立保留。
MAPE 按案例等权，WAPE 按参考周期加权。

| 每 EXU inflight 上限 | 完成带宽无约束 MAPE | 每周期一条完成 MAPE | 无约束 WAPE | 每周期一条 WAPE |
| --- | ---: | ---: | ---: | ---: |
| 无限制，输入值 0 | 8.444% | 4.976% | 4.023% | 3.634% |
| 7 | 4.329% | 4.139% | 3.613% | 3.562% |

合并集合中，inflight=7 加每周期一条完成的平均误差最低。
Softmax 子集在 inflight=7 下的误差有所增加，说明完成带宽约束对不同指令流
有不同影响。总周期误差支持评估模型组合，具体硬件完成带宽及其与 inflight
容量的关系仍需独立测量。

## 参数适用范围

默认运行的 13 个计算 opcode/form 均使用已配置的 latency 和 self-II。
运行中记录了 5 个不同 forwarding pair 与 22 个不同 II pair 的缺失回退；
跨案例累计分别有 42 和 154 条去重 warning 记录。逐对信息与使用的默认值见机器报告，
每个案例的完整 warning 保存在 `completion_one_cap7/python_metrics.json`。

5 个 forwarding pair 为：

- `VDUPS.fp32 → VADD.fp32`
- `VEXP.fp32 → VMULSCVT.f32_to_f16`
- `VEXPDIF.fp32 → VCVT_F32_TO_F16.f32_to_f16`
- `VLD.fp32 → VMULS.fp32`
- `VPACK.u32_to_u16 → VSSTB.bf16`

这些回退遵循当前 ParamDB 的规则，构成混合指令流精度结论的适用范围。

## 双实现和调度检查

- 四组设置各运行 12 个用例，得到 48 组 Python/C++ 对照，三个周期和计数字段全部一致。
- C++ 直接读取 12 个原始 CCE 文件，结果与 Canonical 输入一致。
- 每周期一条完成的两组 Python 运行均观测到完成周期冲突 0。
- 四组 Python 运行的 self-II 违规和同端口同周期重复发射均为 0。
- 每条观测到的计算指令满足 `done_cycle = issue_cycle + latency`。
- 源码、CCE 输入、基础配置、实际 Canonical 输入与参考日志均保留 SHA256。

三个对照字段为 `vf_end`、`cycles_executed`、`linear_inst_count`。
完成带宽无约束设置中，Python 观测到候选完成周期重合次数为 cap=0 的 1511 次、
cap=7 的 1244 次；计数单位为与同端口已有完成周期重合的后续指令。
这些计数描述模型内的调度行为，硬件资源归属需要结合硬件事件判断。

## 证据与复现

[机器报告](../../results/softmax_completion_20261020/analysis.json) 保存聚合误差、
逐例结果、分组误差、33 个案例的合并统计、峰值 inflight 和参数回退。
同目录的 `inputs.json` 保存 CCE、参考日志及 Canonical 输入的路径和 SHA256，
`implementations.json`、`source_sha256.json` 保存两种完成带宽实现及固定配置的指纹。

每组的 `python_metrics.json`、`native_metrics.json` 保存完整指标，
`native_cce_metrics.json` 保存 C++ CCE 入口对照。
各例 Native stdout/stderr 与运行进度也保存在相应子目录。

在仓库根目录执行，runner 指向本次构建：

```bash
python3 results/softmax_completion_20261020/run.py --backend python --completion one --cap 7
python3 results/softmax_completion_20261020/run.py --backend native --completion one --cap 7 --runner /path/to/vfsim_native_json_runner
python3 results/softmax_completion_20261020/run.py --backend native --completion one --cap 7 --cce --runner /path/to/vfsim_native_json_runner
python3 results/softmax_completion_20261020/analyze.py
```

`--cap 0` 运行无限制 inflight 对照。`--completion unlimited` 使用对应的完成带宽
无约束源码目录或 runner，通过 `--source-root`、`--runner` 指定。
脚本校验实现、参数和输入指纹；汇总脚本读取四组保存的指标。
