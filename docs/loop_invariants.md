# 循环无关指令外提

`core/execution/optimization.cpp::hoistLoopInvariants()` 在 Canonical 层识别循环无关的纯计算，
并在动态展开前将其移动到循环之前。规则与 Python 实现共享 Catalog 的 `pure` 声明。
固定 PSET、PLT/PLTM、广播及普通纯计算链可以外提；变化输入、反馈计数、合并目的、
volatile、访存、未知效果和零次循环保留位置。嵌套循环中的指令按实际依赖的循环逐层外提。

`CanonicalValue.scalarExpression` 保存标量表达式，`scalarDependencies` 引用读取的 Scalar
definition，`scalarLoopDependencies` 引用相关 loop ID。CCE adapter 为局部声明及赋值记录
这些关系；标量表达式不产生计时 uop。Canonical JSON 使用对应的 snake_case 字段。

外提保留 instruction ID、definition ID、源位置和循环出口绑定。动态展开为共享结果计算
实际生存期。`SimulationResult.compilerOptimizationStats` 记录启用状态、唯一指令数及每条
移动的来源和目标循环。已描述编译后位置的输入可以设置 `source.compiler_optimized=true`。

固定测试见 [loop_invariants.cce](../VFtest/cce/contracts/loop_invariants.cce)。
A5 CANN 9.0.0-beta.1 的 18 类情况、每类两组输入均通过数值、保护区及指令次数校验；
原始测量与 Python/C++ 入口对照由 MultiSoC-membar-refactor 分支记录。
外提仅用于 Catalog 标记纯度且输入可证明不变的计算，完整编译器的常量折叠、死代码删除、
循环剥离及寄存器压力决策具有独立规则。

分支与安装包检查见[分支验证](validation/loop_invariants_sync_20261103.md)。
