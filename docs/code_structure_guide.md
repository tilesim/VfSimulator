# VfSimulator 代码结构

Python 仿真器由输入、执行控制、微架构组件、参数查询和日志输出组成。
`main.py` 组合 `InputAPI` 与 `CoreVfCostModel`，命令行和库调用共享预测流程。

## 调用链

```text
CCE / Canonical JSON / CanonicalVfInfo
                  │
          api/input_api.py
       解析、构造与统一语义校验
                  │
          CanonicalVfInfo
                  │
     core/simulator.py::run_vf_info
      参数组装 → prepare_program
                  │
           PreparedProgram
                  │
      execution/engine.py::run
      逐周期协调微架构组件
                  │
          周期结果与日志
```

## 目录

```text
api/
  input_api.py
  cce/             源码解析、私有 IR、规范化和值版本化
  json/            Canonical JSON Schema 与编解码
  programmatic/    显式构造 Canonical 的 Builder
  common/          Canonical 类型、Catalog、校验、符号和诊断
core/
  simulator.py
  execution/       指令准备、动态展开、周期控制、loop 和 membar
  uarch/           IFU、IDU、Registers、SHQ、OoO、EXQ、EXU、LSU
  params/          参数加载、结构化记录、查询及缺失参数策略
  trace/           事件记录、结果定义、文件与 Perfetto 输出
configs/           指令语义与微架构时序参数
```

## 输入层

| 文件 | 关键接口 | 职责 |
| --- | --- | --- |
| [input_api.py](../api/input_api.py) | `InputAPI.load_cce/load_json/from_canonical/new_builder` | 接收输入并返回 Canonical |
| [schema.py](../api/common/schema.py) | `CanonicalVfInfo` 及相关 dataclass | 定义共享数据合同 |
| [validator.py](../api/common/validator.py) | `require_valid_canonical_vf_info` | 定义、作用域、循环、访存和依赖校验 |
| [instruction_catalog.py](../api/common/instruction_catalog.py) | `InstructionCatalog`、`InstructionSpec` | 查询指令签名、form 和语义属性 |
| [adapter.py](../api/cce/adapter.py) | `parse_cce_canonical_vf_info` | 组合 CCE 解析、版本化和校验 |
| [serialization.py](../api/json/serialization.py) | `canonical_vf_info_from_dict/to_dict` | Canonical 对象编解码 |
| [builder.py](../api/programmatic/builder.py) | `VfInfoBuilder.build` | 显式构造并校验 Canonical |

输入层的转换和数据字段见[输入 API](input_api.md)。

## 执行控制

| 文件 | 关键接口 | 职责 |
| --- | --- | --- |
| [simulator.py](../core/simulator.py) | `run_vf_info/predict_vf_cycles` | 检查输入类型、合并参数、组装模型并返回结果 |
| [instruction.py](../core/execution/instruction.py) | `DynamicInstruction`、`Uop` | 保存动态指令身份与运行期指令状态 |
| [prepare.py](../core/execution/prepare.py) | `CoreLoweringPass.lower`、`prepare_program`、`ValueStorageLookup` | 检查执行能力、转换表示、分析静态布局和查询声明的存储类型 |
| [expansion.py](../core/execution/expansion.py) | `DynamicExpander.prepare_structured_stream` | 展开循环和 unroll、绑定动态值身份及最后使用 |
| [engine.py](../core/execution/engine.py) | `Engine.run/backend_step/admit/reservation` | 协调周期阶段、传输、资源预留、重命名入队和排空 |
| [loop_control.py](../core/execution/loop_control.py) | `LoopControl.can_dispatch/notify_dispatch` | 控制 block、VLOOP 和迭代开放时间 |
| [membar.py](../core/execution/membar.py) | `accept_membar/update/blocks` | 管理屏障阶段和后续访存门控 |

## 微架构组件

每个组件维护自身状态、局部约束和状态转换。Engine 安排周期阶段并协调组件调用。

| 文件 | 关键接口 | 状态和行为 |
| --- | --- | --- |
| [ifu.py](../core/uarch/ifu.py) | `next_inst/done` | 保存准备好的动态指令流，按顺序交给 IDU |
| [idu.py](../core/uarch/idu.py) | `accept/can_accept/dispatch` | 维护窗口，按循环、信用和地址状态约束顺序分发 |
| [registers.py](../core/uarch/registers.py) | `rename/ready_cycle/on_uop_start/on_uop_done` | 动态值映射、依赖、释放事件、对齐状态和信用反馈 |
| [register_bank.py](../core/uarch/register_bank.py) | `allocate/can_free/try_free` | 管理 vector/predicate 物理资源池及安全回收 |
| [shq.py](../core/uarch/shq.py) | `enqueue/update_ready/reserve/release_credit` | 维护计算等待指令及 compute/store 共用信用 |
| [ooo.py](../core/uarch/ooo.py) | `dispatch/issue_direct_from_shq` | 选择就绪计算指令、安排端口、执行轮转和 EXU0 预留策略 |
| [exq.py](../core/uarch/exq.py) | `enqueue/can_accept/predict_issue_cycle/pop_ready` | 每端口 FU 队列、容量、接收延迟和队首选择 |
| [exu.py](../core/uarch/exu.py) | `can_accept/can_issue/next_issue_cycle/start/finish` | 管理端口、II、在执行容量、开始和完成 |
| [lsu.py](../core/uarch/lsu.py) | `enqueue/update_ready/_issue_ready_lsu/finish` | 维护 LSQ、访存依赖及共享 UB 发射预算 |

`idu.py` 中的 `DispatchBudget` 保存本周期可用额度；`AddressStateTracker` 记录指针更新
的就绪时刻。`registers.py` 中的 `AlignProducerRecord`、`AlignGeneration` 记录特殊
对齐状态的生产者和消费分组。

## 参数模块

| 文件 | 职责 |
| --- | --- |
| [loader.py](../core/params/loader.py) | 选择配置路径、读取 JSON、检查参数格式和类型 |
| [types.py](../core/params/types.py) | 定义 `UarchParameters`、`InstructionProfile`、`VfTiming` 和 `LoadedParameters` |
| [database.py](../core/params/database.py) | 解析并缓存指令 Profile，查询 forwarding 与 II，收集缺失参数诊断 |
| [uarch.py](../core/params/uarch.py) | 合并基础配置和 VF override，检查模型端口范围，应用理论场景 |
| [fallback.py](../core/params/fallback.py) | 选择 form 候选和缺失时序的默认值 |

参数的字段和加载优先级见[配置参数](configuration.md)。

## 输出模块

[recorder.py](../core/trace/recorder.py)保存指令开始、完成及阻塞事件。
[writer.py](../core/trace/writer.py)定义 `SimulationResult` 并写入结果文件。
[perfetto.py](../core/trace/perfetto.py)将执行事件转换为 Perfetto 时间线。

结果字段、日志格式和检查方法见[验证与精度](validation.md)。

## C++ 子项目

`native/` 包含 C++ 的输入层、仿真核心、命令行入口、测试和 CMake 构建。
两种实现按相同的职责划分目录：

| Python | C++ | 职责 |
| --- | --- | --- |
| `api/input_api.py` | `native/api/input_api.h/.cpp` | 接收与校验 Canonical 输入 |
| `api/common/` | `native/api/common/` | 数据合同、Catalog、通用与指令校验 |
| `api/json/` | `native/api/json/` | Canonical JSON 输入 |
| `core/simulator.py` | `native/core/simulator.h/.cpp` | 参数生命周期、组件组装和预测入口 |
| `core/execution/` | `native/core/execution/` | 准备、展开、周期协调、循环与屏障 |
| `core/uarch/` | `native/core/uarch/` | 各组件的状态与局部时序 |
| `core/params/` | `native/core/params/` | 参数加载、Profile 和查询缓存 |
| `core/trace/` | `native/core/trace/` | 事件记录、结果和 Perfetto 导出 |
| `main.py` | `native/cli/json_runner.cpp` | 命令行入口 |

C++ 使用 `.h/.cpp` 声明和实现模块；IFU、SHQ 和寄存器 bank 的小型实现位于头文件。
两种核心都通过 Uop 的 Profile 引用取得执行时序和端口约束。
CCE 解析与 Python Builder 位于 Python 输入层；C++ 接收转换后的 Canonical JSON
或通过 C++ API 校验的 CanonicalVfInfo 对象。
