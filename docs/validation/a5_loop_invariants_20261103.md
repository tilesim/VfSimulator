# A5 循环外提验证

原始测量与本记录中的双实现回归来自 `MultiSoC-membar-refactor` 分支；本分支使用相同的循环外提规则。

2026-11-03 使用 CANN `9.0.0-beta.1`、`dav-c310-vec` 和
`Ascend950PR_9599` C Model 验证编译器的循环外提行为。探针覆盖 18 类情况，
每类使用两组输入，36 组通过输出数据及保护区的逐字节校验。
浮点向量数据使用 FP32，PLT 计数及标量变化广播使用 uint32。

编译选项固定为 `-O2 -g --cce-aicore-arch=dav-c310-vec --cce-aicore-only`
`--cce-simd-vf-fusion=false -mllvm -cce-aicore-vec-misched=0`。
循环显式使用 `unroll(1)`，普通循环执行四次，嵌套循环为外层两次、内层四次。
每轮结果写入独立地址，PLT 更新后的计数也写回校验。外部计数由 UB 读取并传入 VF，
输入向量每轮不同，结果保持可观察。

## 动态指令观测

按 popped 日志中的 VF PC 区间及动态 ID 窗口选择事件，再统计目标 RV opcode。
两组输入的次数一致。表中次数已经与 VFSim 优化后的动态指令流逐项核对。

| 情况 | 目标指令 | C Model / VFSim 次数 | 结论 |
| --- | --- | ---: | --- |
| 固定 PSET pattern | 部分 mask PSET | 1 | 移到循环前 |
| 每轮将计数重置为字面常量 | PLT | 1 | mask 与更新后计数固定 |
| 每轮将计数重置为外部参数 | PLT | 1 | 外部参数保持不变 |
| PLT 的更新计数反馈至下一轮 | PLT | 4 | 保留反馈 |
| 计数依赖当前循环变量 | PLT | 4 | 每轮重新生成 mask |
| 每轮重置为外部参数加常量 | PLT | 1 | 标量表达式保持不变 |
| 固定 start 与外部 count | PLTM | 1 | 移到循环前 |
| start 依赖当前循环变量 | PLTM | 4 | 每轮执行 |
| 固定标量及固定 mask 广播 | VDUPS | 1 | 移到循环前 |
| 标量随循环变量变化 | VDUPS | 4 | 每轮执行 |
| 广播的 mask 随循环变化 | PLT / VDUPS | 4 / 4 | 保留依赖链 |
| 每轮从不同向量广播 | VDUP | 4 | 每轮执行 |
| 固定 PLT → PNOT → 广播 | PLT / PNOT / VDUPS | 1 / 1 / 1 | 整条纯计算链外提 |
| 只依赖外层变量的内层 mask | PLT | 2 | 每次外层迭代生成一次 |
| 零次循环 | PLT | 0 | 保持零次执行 |
| 显式放在循环前的对照 | PLT | 1 | 保持原位置 |
| 输入向量在循环前固定 | VADDS | 1 | 普通纯计算也可外提 |
| 累加结果反馈至下一轮 | VADDS | 4 | 保留反馈计算 |

PSET 探针另外包含一条在循环前生成的 ALL store mask，因此日志中的 PSET 总数为 2。
标量变化广播使用 uint32 STORE，保持类型和位表示。原始实验目录同时保留其早期
向量重解释版本的两次数值通过记录，汇总选用 uint32 STORE 的两组结果。

## 实现验证

[循环外提 pass](../loop_invariants.md)处理 Canonical 数据，保留静态身份、标量反馈、
嵌套边界和循环出口。36 组源码均通过 Python 输入层；C++ 分别通过 CCE 和 Canonical
JSON 两种入口执行，预测周期及全部移动记录与 Python 一致。

自动化测试另覆盖合并模式的旧目的依赖、volatile、未知调用、访存保留、标量复合赋值、
同名变量、跨两层外提、JSON 往返以及 unroll 后的出口消费者。
内嵌自增和赋值表达式会被 CCE adapter 拒绝。显式标量赋值记录 SSA 关系。
Python 的空 unroll 循环会继续处理后续指令，保留外提结果的出口绑定。
启用 Native runner 的完整 Python 测试为 536 项通过，C++ CTest 为 9 项通过。

这些实验验证外提与动态执行次数。完整编译器的常量折叠、死代码删除和压力驱动决策
具有各自的处理规则，当前 pass 的纯度声明与边界见设计文档。

## 算子回归

固定当前 A5 参数、两类寄存器完成带宽和 inflight=7，对照
`source.compiler_optimized=true` 与默认外提流程。25 个 Canonical 回归案例及全部
12 个 Softmax CCE 案例，共 37 个案例的周期全部一致。这些固定输入的候选指令位于
循环前，或存在循环依赖，本次未移动指令。

| 参考集合 | 有 C Model 参考的案例数 | 启用前 MAPE | 启用后 MAPE |
| --- | ---: | ---: | ---: |
| Canonical 回归 | 21 | 4.6323% | 4.6323% |
| Softmax CCE | 12 | 3.3940% | 3.3940% |
| 合计 | 33 | 4.1820% | 4.1820% |

另外四个 Canonical 案例缺少参考周期，保留两种流程的周期对照。
本次实验为 A5 编译器证据；A6 使用同一 pass 的输入与依赖规则。

## 证据与复现

- [外提与双实现机器记录](../data/a5_loop_invariants_20261103.json)：36 组数值状态、
  VF 动态次数、源码 SHA256、模型指令次数及逐条移动记录。
- [固定参数回归记录](../data/a5_loop_invariants_regression_20261103.json)：37 个案例的
  输入指纹、参考周期、两种流程的预测及误差，附 A5 配置 SHA256。
- 本地 `results/a5_loop_invariants_20261103/evidence/` 保存源码、编译/运行命令、
  目标文件、输入、golden、输出、校验和 active-core 原始日志。
  同级 `artifacts.json` 保存文件 SHA256。
- 同级 `model/` 保存逐例 Canonical 输入及 C++ 两种入口的输出；`regression/` 保存
  逐例算子结果。归档根目录保存回归和双实现校验脚本，`verification/` 保存检查日志。

```bash
python3 skills/vfsim-instruction-benchmark/scripts/loop_invariants.py \
  --out /tmp/vfsim-a5-loop-invariants --replicas 2 --batch-size 6 --jobs 2
python3 -m unittest tests.test_loop_invariants
VFSIM_NATIVE_RUNNER=/path/to/vfsim_native_json_runner \
  python3 -m unittest tests.test_native_contract_parity
```
