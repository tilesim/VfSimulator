# A5 VFtest 算子指令对

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`。
输入范围为 `VFtest/cce`、`VFtest/canonical`、`VFtest/json` 中的完整算子和依赖探针。
`contracts`、`ordinary_instructions`、`fixtures`、`cce_expected`、`membar_timing` 不在本次范围。

## 参数需求

`operator_pair_inventory.py` 使用 InputAPI 和实际 Core lowering 展开每个输入。
II 需求取同一输入内 COMPUTE opcode/form 的有向矩阵，只保留具有共同 EXU 端口的组合；
各输入的需求再取并集。矩阵包含初始化、循环和尾部的指令。
forwarding 需求按展开后的 definition_id 和完整循环 iteration_path 追踪向量、谓词 RAW。
UB 地址与标量不作为架构向量寄存器 RAW 统计。

清单生成时关闭动态指令数量保护，以容纳长链输入；文件内容和模型运行默认值保持固定。
同语义的多种输入形式同时保留文件记录，另提供去重语义哈希。

## 数值校验与 II

独立输入取可表示的有限值；每个目标输出独立存储。FP32/FP16 使用各自容差，
整数索引、谓词和保护字节精确比较。归约只检查硬件定义的结果 lane；VCMAX 同时检查索引。
PSET/VCMP 谓词按元素字节宽度生成稀疏 bit，而非将一个元素覆盖的全部 bit 置位。

PSET 无向量源，使用长的可观察预热链形成前端背压，预热独立源后再观察目标流。
其他计算使用共同谓词门。成对交错和反向成对顺序增加两个端口上的双向转换样本。
VDUPS 的运行时标量来自 UB，DMA 完成后同时同步 PIPE_V 和 PIPE_S。
`--warm-operation vadd` 使用 FP32 加法链，为忙端口留下连续空闲发射槽；
目标包含 VADD 时使用 VSUB 链，保持支持指令与目标可区分。
`--warm-operation vor` 使用位运算链。
`--warm-lead 1` 在预热链前增加独立 VOR 并校验其输出，用于改变预热链的端口相位；
实际分析仍按端口区分，预热指令介入的转换不进入目标 pair 的相邻样本。

`--store-immediately` 在每个目标后保存结果，缩短结果谓词的活跃区间。
`--wide-predicates` 提供六组独立 PAND 操作数，配合六条流增加双向端口样本。
`--runtime-scalar-operands` 在 VF 外从 UB 读取 PLT 计数和 VCI 起点，标量 DMA 同步使用 PIPE_S。
`--cases` 接收保存的 II 探针清单，复测时保留顺序、预热长度和各组流数。
批次最多包含 28 个 4 KiB 案例；大于 16 个时，输出窗口从 UB `0x20000` 开始，
输入、输出和栈保留空间分别核对边界。

VPACK 使用 `--gate-unmasked` 在源向量上增加 VOR 依赖门，数值保持原样。
`--streams 1` 提供归约和多周期转换的孤立对照。

单周期发射证明每端口每周期一条的间隔下界。多周期候选读取 ISU 的
`src_rdy`，要求当前指令的全部源在下一个候选槽前可用，包括 forwarding。
缺少该字段的日志保守要求源在前一条发射前完成；同时要求
IDU 提前分发、旧的同 opcode/form self-II 已释放；排除在途容量和更早无关指令的完成周期冲突。
相邻两条自身的固定 latency 若使单周期间隔发生完成冲突，保留该 pair 的有效最小转换间隔，
并在证据中标记 `preceding_pair_completion_conflict`。该数值同时表达此 pair 的发射可行性；
单凭这些样本无法分离发射流水线与完成端口对它的贡献。
两个可用端口须各有至少两次相同最小间隔观测；单端口指令按其可用端口检查。
只分析同端口真实相邻发射，中间插入的支持计算不会被跳过。

## forwarding

二元 consumer 分别检查 Vn、Vm，单源检查 Vn，STORE 检查 Vd。
真实 RAW 源须来自指定 producer，其他源须在 producer 发射前完成。
每个源位置至少两次重现最小值，pair 配置取各源位置最小值的最大值。

LOAD 探针使用 VOR 链预热 mask 和其他输入，并检查 popped 等于 LSU I1，排除队列等待。
VLD 使用运行时次数为 1 的保留循环，满足 VAG 的编译约束。
计算→STORE 使用单对照并优先存储 consumer；VSSTB 校验块步幅 2 的写入区和间隙哨兵。

## 复现

在仓库根目录运行：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/operator_pair_inventory.py --out /tmp/operator-inventory.json
python3 skills/vfsim-instruction-benchmark/scripts/operator_pairs.py --inventory /tmp/operator-inventory.json --out /tmp/operator-ii --gate-unmasked --jobs 2 --batch-size 8
python3 skills/vfsim-instruction-benchmark/scripts/operator_forwarding.py --inventory /tmp/operator-inventory.json --out /tmp/operator-fwd --jobs 1
PYTHONPATH=. python3 skills/vfsim-instruction-benchmark/scripts/analyze_operator_pairs.py --runs /tmp/operator-ii /tmp/operator-fwd --out /tmp/operator-parameters.json
```

实验使用补参前的清单选择缺口；参数补齐后，新清单不再包含这些待测项。
分析工具生成候选参数及逐事件证据，配置录入时只合并经确认的缺失 pair。
编译拒绝、运行超时和数值失败保留诊断；无法隔离的观测保留为上界。

## 本轮结果

74 个输入文件、56 个去重语义程序需要 325 组 II、103 组实际 RAW forwarding，均有配置覆盖。
新增 162 组实测 II、10 组实测 forwarding；29 组 PSET RAW 使用已有 producer 默认规则。
686 个有效数值案例通过，初始模板失败案例、编译失败及超时保留为诊断。
完整计数、精度与范围见 [算子覆盖报告](../../../docs/instruction_coverage/a5/operator_pairs.md)。
