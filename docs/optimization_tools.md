# 仓库内优化工具

`optimizer/` 提供 DAG 分区、loop 切分、候选计划搜索和展开工具。
`tools/` 提供源码生成、结果分析、绘图及回归脚本。
本页说明工具职责和使用前提，微架构预测入口见[输入 API](input_api.md)。

## 模块职责

| 文件 | 内容 |
| --- | --- |
| [dag_lib.py](../optimizer/dag_lib.py) | DAG 节点、依赖和拓扑遍历 |
| [partitioner.py](../optimizer/partitioner.py) | 按计算深度切分，分析边界 live-out 和分区宽度 |
| [split_only_optimizer.py](../optimizer/split_only_optimizer.py) | 生成边界搬运、复用中间 UB 槽、计算候选分数和局部搜索 |
| [generic_heuristic_split_optimizer.py](../optimizer/generic_heuristic_split_optimizer.py) | 根据宽度变化、高 latency 节点和 live-out 压力生成 seed |
| [generic_trip_aware_split_optimizer.py](../optimizer/generic_trip_aware_split_optimizer.py) | 将 trip count 纳入初始切分粒度选择 |
| [stage_c2_segment_unroll_optimizer.py](../optimizer/stage_c2_segment_unroll_optimizer.py) | 分段展开计划和候选生成 |

`neighbors()` 通过增加、移除或移动 cut 生成候选，`hillclimb()` 比较分数并迭代计划。
分区输出包含边界存储/加载和中间 UB 容量信息。trip-aware 策略在 seed 阶段使用
循环次数；候选评分和局部搜索继续决定最终计划。

## 仿真评估的使用前提

优化器的仿真评估调用需要适配 Canonical 输入和 `CoreVfCostModel` 接口。
当前 `split_only_optimizer.py` 的评估依赖尚未接入这些接口；该路径的执行能力需在
适配后验证。候选生成与分区代码可用于研究计划结构。

接入预测时，生成程序应显式声明值、UB 对象、producer、循环携带、访存模式和屏障，
经 API 校验后交给 Core。计划的依赖、地址范围、UB 复用与数值正确性需要同时验证。

## 计划验证

计划分析包含各段 count/unroll、边界传输字节数、中间 UB 容量、最大活跃定义和周期。
分数中的 cycle、切分代价及容量罚项分别记录。比较候选时使用相同输入、配置、
编译选项和计时口径，并用独立 golden 检查切分后的结果。

`tools/run_cost_model_regression.py` 的 Canonical simulate 用例用于检查预测稳定性。
CAModel 探针、数值验证和参数校准流程见[验证与精度](validation.md)。
