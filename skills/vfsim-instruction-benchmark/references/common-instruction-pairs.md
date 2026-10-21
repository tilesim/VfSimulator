# A5 常用跨指令 II 与 forwarding

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`、
`__NPU_ARCH__=3101`。配置采用 `OP.dtype` 精确键，Python/C++ 共用参数。
范围为 FP32、FP16、BF16 中具有原生单指令实现的常用组合。

## 探针范围

选择参考 `VFtest/canonical/` 中 39 个具有指令的输入文件，排除
`cce_expected/` 和明确标注的非法 fixture。静态 RAW 频次用于定位常见链路，
循环次数和 unroll 不参与频次加权。清单保存在原始产物的 `pair-priorities.json`。

计算指令选择加减、乘法、标量立即数运算、min/max、绝对值、ReLU 和
exp/log/sqrt/div 等组合。两种方向的 II 分别测量。
forwarding 另测 VLDS→计算、计算→VSTS，以及 VCMP_GT/VCMP_EQ→VSEL 的谓词路径。
完整 API 对列表由 `scripts/instruction_pairs.py` 中的三个常量表定义。

`VMULS.bf16` 的 CANN wrapper 展开为 VDUPS+VMUL，作为复合序列保留，
本批单指令对参数只接受单条目标 RV 指令实现。
该批常用组合的测量不代表完整 opcode/dtype 笛卡尔积。

## 测量结果

478 个数值案例全部通过，确认 **99 组 forwarding、70 组 II**。
其中新增 33 个 forwarding 精确参数和 26 个 II 精确参数；其余与已有配置一致。
每组按有方向的 opcode/dtype 对计数。下表单元格为 `forwarding / II`，单位为 cycle。
`—` 表示本批未测该项或该 dtype 没有原生单指令实现；访存和谓词路径的 II 不在本批 EXU 指令对测量范围内。

| 指令对 | FP32 | FP16 | BF16 |
| --- | ---: | ---: | ---: |
| VADD→VMUL | 3 / 1 | 3 / 1 | 3 / 1 |
| VMUL→VADD | 4 / 2 | 4 / 2 | 4 / 2 |
| VADDS→VMUL | 3 / 1 | 3 / 1 | 3 / 1 |
| VMUL→VADDS | 4 / 2 | 4 / 2 | 4 / 2 |
| VEXP→VMUL | 13 / 1 | 18 / 1 | — / — |
| VSQRT→VMUL | 14 / 1 | 19 / 1 | — / — |
| VLN→VMUL | 15 / 1 | 20 / 1 | — / — |
| VCMP_GT→VSEL | 2 / — | 2 / — | 2 / — |
| VLDS→VMUL | 6 / — | 6 / — | 6 / — |
| VMUL→VSTS | 6 / — | 6 / — | 6 / — |

全部指令对、各源位置最小值及端口重复数见机器证据。

## 数值与编译检查

每个用例使用不同的可表示有限输入，Python golden 逐运算量化到目标 dtype。
全部目标结果写回 UB；compare→select 通过两个不同向量的选择结果观察谓词。
同时检查输出字节、保护区、目标动态 RV 指令数量及实际源寄存器。

每个案例包含独立输入流；两份 replica 使用不同输入。
普通 forwarding 对二元 consumer 分别测试 Vn、Vm，VSEL 测 Pg，VSTS 测 Vd。
编译器实际日志的 `_FP`、`_INT` 后缀按 opcode 编码域归并。
重放分析保留原始 contract 与哈希，并单独记录识别出的物理拼写。

## forwarding 隔离

预热输入、完成访存屏障后，通过单独加载的谓词与 VOR 恒等运算开放 producer。
consumer 的其他向量源和普通 full mask 在 producer 发射前完成。
LOAD→计算另外检查真实 LOAD 的结果源，compare→select 检查真实谓词源。
只有实际 producer→consumer 依赖、其他源已完成且对应目标源位置的样本可参与取值。

每个源位置至少重复观察两次最小间隔。一个指令对的单值配置取各源位置最小间隔的最大值。
计算→STORE 的连续流可能包含 LSU 排队，另运行 `--streams 1` 的孤立对照；
单对照至少两次重现最小间隔后才写入该 forwarding pair。

## II 隔离

使用不同目的、无 RAW 的目标计算流，通过共同谓词门批量开放；分别生成交替和分块顺序。
分析同一 EXU 端口上相邻的实际目标发射，反向 pair 单独记录。
两个可用端口均须有至少两次相同最小间隔观测。

实际观察到一周期连续发射时，已经达到该模型每端口每周期一条的最小间隔。
共享谓词经 forwarding 提前可用的样本也能证明这一周期下界。
多周期候选则要求 consumer 的全部源在前一条发射前完成，IDU 提前分发，
并检查更早同类指令的 self-II，剔除其流水线等待。

EXQ 按端口同时检查相邻指令转换 II 和 opcode/form 的 self-II 历史。
每个 EXU 每周期接收一条指令，默认允许 7 条在途计算，并预留每周期一条的完成带宽。
源就绪、self-II、转换 II、执行容量及完成带宽分别约束发射。
完整算子中的混合 form 参数与覆盖统计见 [VFtest 算子指令对](operator-pairs.md)。

## 复现与产物

在仓库根目录运行：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/instruction_pairs.py --kind ii --jobs 1 --out /tmp/common-ii
python3 skills/vfsim-instruction-benchmark/scripts/instruction_pairs.py --kind fwd --jobs 1 --out /tmp/common-fwd
python3 skills/vfsim-instruction-benchmark/scripts/instruction_pairs.py --kind fwd --streams 1 --pairs vadd:vsts vadds:vsts vsub:vsts vmul:vsts vmuls:vsts vmax:vsts vexp:vsts vsqrt:vsts vln:vsts vdiv:vsts --out /tmp/common-store
python3 skills/vfsim-instruction-benchmark/scripts/instruction_pairs.py --kind ii --pairs vsub:vmax --replicas 4 --out /tmp/common-ii-repeat
python3 skills/vfsim-instruction-benchmark/scripts/analyze_instruction_pairs.py --runs /tmp/common-ii /tmp/common-fwd /tmp/common-store /tmp/common-ii-repeat --out /tmp/common-pairs.json
```

分析默认只生成报告。核对 `changes` 与 `conflicts` 后，使用 `--apply` 写入配置。
`--replace` 仅接受明确复核的配置路径。

精简证据在 [a5-common-instruction-pairs.json](evidence/a5-common-instruction-pairs.json)，
其中包含数值校验、动态 ID、源位置、端口、重复样本、参数变化和运行文件 SHA256。
原始源码、输入/golden/输出、命令与动态日志保存在
`results/a5_common_pairs_20261020/`，由 `manifest.json` 索引。

## 项目验证

411 项 Python 测试、8 项 C++ CTest、224 条软件展开规则的双实现对照通过。
22 个 smoke 用例的 Python/C++ 开始、完成周期一致，VF 周期与既有基线一致。

VCMAX 与 VADD/VEXP 的 FP32 补测、共享资源阻塞与 II 分组建议见
[FP32 执行资源实验](fp32-fu-contention.md)。
