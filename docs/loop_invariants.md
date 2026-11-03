# 循环无关指令外提

`core/execution/optimization.py::hoist_loop_invariants()` 在 Canonical 程序上识别
循环无关计算，将指令移到对应循环前。Python 和 C++ 使用相同的输入合同与规则。
CCE、Canonical JSON 和直接构造的对象共享该处理路径。

```text
API 校验的 CanonicalVfInfo
    → 循环无关指令外提
    → Core 表示转换
    → 循环与 unroll 展开
    → 动态值身份及寄存器生命周期
    → Engine
```

## 外提条件

Catalog 的 `pure: true` 标识输入、输出和配置能够完整描述计算结果的指令。
当前声明覆盖 PSET、PLT/PLTM、谓词逻辑、广播、移动、选择和部分普通算术与逻辑计算。
指令需要有输出，所有输入需要在目标循环中保持不变。访存、Membar、MOVP、volatile
读取和未声明纯度的操作保留位置。LOAD 即使使用固定地址也保留在循环中。

pass 自内向外处理循环，反复扫描直接 body，直到找不到新的外提指令。
一个生产者外提后，其纯计算消费者可以继续外提。依赖外层变量的指令可以移出内层
循环，并留在外层 body。只有能够确定 trip count 为正的循环参与外提；零次及未知次数
循环保持原结构，避免提前执行原本可能不执行的计算。

循环 entry/back-edge 关系标识反馈值。普通寄存器、谓词以及 PLT 更新的标量计数
均参与依赖判断。合并模式使用的旧目的值也作为输入，存在反馈时保留在循环中。

## 标量依赖

CCE 私有 IR 的 `AdapterScalarAssignment` 记录局部标量初始化与赋值。
值版本化为它们创建独立的 Scalar definition，并在 `CanonicalValue` 上保存：

| 字段 | 含义 |
| --- | --- |
| `scalar_expression` | 定义该标量的源码表达式，供来源追溯 |
| `scalar_dependencies` | 表达式读取的 Scalar definition ID |
| `scalar_loop_dependencies` | 表达式依赖的 Canonical loop ID |

表达式只用于依赖描述，执行模型不求值这些表达式。标量初始化和赋值不产生计时 uop。
调用、未知符号和 volatile 来源在所在循环内按变化值处理。
循环局部声明具有独立逻辑身份，可区分同名内外层变量。
JSON 与 Builder 支持相同字段，API 检查引用的 Scalar value 与 loop 是否存在。
直接构造的输入需要准确声明每轮变化的标量及反馈关系。

下面两个循环具有不同的数据流：

```c++
for (...) {
    uint32_t n = count;
    p = plt_b32(n, POST_UPDATE);
}
```

每轮读取同一个 `count`，产生相同 mask 和更新后的计数，PLT 可以外提。

```c++
uint32_t n = count;
for (...) {
    p = plt_b32(n, POST_UPDATE);
}
```

下一轮读取上一轮更新的 `n`，PLT 保留在循环中。
`n = i + 13` 具有当前循环依赖，同样保留在循环中。

## 身份、结果与边界

外提保留 instruction ID、definition ID、源位置和循环出口绑定。
动态展开为外提结果创建循环外的动态实例，全部迭代及循环后的消费者引用该实例。
物理寄存器按实际最后使用位置释放，外提结果的生存期会相应延长。
空循环的 unroll 展开继续处理后续指令及出口绑定。

返回结果的 `compiler_optimization_stats` 包含 `enabled`、
`hoisted_instruction_count` 以及逐条 `moves`。每条记录含 instruction ID、opcode、
来源 loop ID 和目标父 loop ID，顶层目标使用 null。跨越多个循环的同一指令可以产生多条
move，指令总数按 ID 去重。C++ `SimulationResult.compilerOptimizationStats` 保存对应信息。

已描述编译后指令位置的输入可以设置 `source.compiler_optimized=true`，保留输入中的
显式执行次数。该字段属于输入来源元数据。固定纯计算集合以外的优化、死代码删除、
循环剥离、常量折叠和寄存器压力驱动的编译决策超出本 pass 的范围。

固定例子见 [loop_invariants.cce](../VFtest/cce/contracts/loop_invariants.cce)，
A5 实验与模型对照见 [验证记录](validation/a5_loop_invariants_20261103.md)。

分支与安装包检查见[分支验证](validation/loop_invariants_sync_20261103.md)。
