# 参数配置

`core/params/loader.*` 读取配置，`database.*::ParamDB` 提供指令 Profile、forwarding 和 II 查询，`uarch.*` 解析资源配置并应用 VF 覆盖。结构化参数定义位于 `types.h`。

| 文件 | 内容与使用方式 |
| --- | --- |
| `configs/instruction_catalog.json` | opcode、操作数、form、mode、对齐操作及访存预算的语义声明 |
| `configs/uarch_override_schema.json` | Canonical 配置覆盖的字段类型与支持范围 |
| `configs/uarch.json` | 队列、寄存器、端口、带宽、循环及屏障参数 |
| `configs/isa.json` | 各指令 form 的 latency、FU、端口与 VF startup/drain |
| `configs/forwarding.json` | producer 到 consumer 的数据可用间隔 |
| `configs/InitiationInterval.json` | 前后指令在同端口的发射间隔 |

Catalog 和覆盖字段表编译在 `api/generated/*.inc` 中；变更其语义声明时同步维护对应数据表并重新构建。其余四类参数文件在模型构造时读取。

## 文件选择与快照

环境变量 `ISA_JSON_PATH`、`UARCH_JSON_PATH`、`FORWARDING_JSON_PATH` 和 `II_JSON_PATH` 可分别指定参数文件。显式环境路径优先，其次查找模型 `baseDir` 下的 `configs/` 和根目录候选文件。ISA 和 uarch 必须存在，forwarding/II 缺失时采用查询回退规则。格式、类型或表格单元无效时抛出异常。

`CoreVfCostModel` 构造时加载只读 `ParamBundle`。同一实例连续调用 `runVfInfo()` 复用快照；配置文件和路径环境变量的更新在创建新模型后生效。

每次运行创建独立的 `ParamDB`、指令与 forwarding 查询缓存、警告记录和微架构状态。VF 的 `uarch` 覆盖仅作用于本次运行。forwarding 缓存键包含 producer 与 consumer 的 opcode/form，缓存值为相对周期数。

`resolveUarch()` 合并结构化基础配置和 Canonical 标量覆盖，并检查资源约束。`membar_timing` 对象在基础配置中设置。未显式提供的资源字段使用 `UarchConfig` 的默认值。

## 时序查询

`ParamDB::inst()` 返回缓存的 `InstructionProfile`，提供 latency、FU、指令类别和允许的 EXU 端口。form 优先精确匹配，支持 `b16→fp16`、`b32→fp32` 备选。缺失指令参数采用 latency=9，计算指令默认使用 ALU 和两个 EXU 端口，并记录警告。

forwarding 优先查询指令对及 producer 的 wildcard 项。缺失 LOAD→STORE 使用 6 cycles，其他缺失对使用 producer latency 减配置 offset，并截断到非负值。II 缺失时，若前条 latency 比后条大 1 则取 2，其他情况取 1。警告通过 `model_warnings.json` 输出。

理论场景通过 `CoreVfCostModel` 构造参数 `vloopOnly`、`directIssue` 或对应的 `theoretical_limit_*` 配置字段启用，用于放宽结构资源后的周期比较。
