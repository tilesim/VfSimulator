# VfSimulator 代码结构与关键函数

本页描述 `dev_wch/refactor` 的 Python 实现。Core 只接受 `CanonicalVfInfo`，
CCE/JSON 读取和程序化构造留在 API。配置、Native C++ 与优化器留待后续重构。

## 调用链

```text
CCE / Canonical JSON / Builder
          ↓ api/input_api.py
     CanonicalVfInfo
          ↓ core/simulator.py::CoreVfCostModel.run_vf_info()
     validate → compatibility → lower → static layout → dynamic expansion
          ↓ PreparedProgram（动态指令、值声明、循环边界）
     execution/engine.py::Engine.run()
          ↓
     周期结果 / 日志 / Perfetto
```

`main.py` 直接调用 `CoreVfCostModel`，命令行与库调用使用相同模型组装和运行过程。
Core 不读取输入文件，不接受旧 VFInfo、旧 `program/src/dst` JSON 或内部 lowering payload。

## 输入层

| 文件 | 主要类/函数 | 职责 |
| --- | --- | --- |
| [input_api.py](../api/input_api.py) | `InputAPI.load_cce/load_json/new_builder` | 统一输入入口 |
| [common/schema.py](../api/common/schema.py) | `CanonicalVfInfo`、value、operand、loop、memory、location 类型 | 共享数据合同 |
| [common/validator.py](../api/common/validator.py) | `validate_canonical_vf_info` | 通用语义、定义与循环作用域校验 |
| [common/instruction_catalog.py](../api/common/instruction_catalog.py) | `InstructionCatalog`、`InstructionSpec` | 指令签名和语义声明 |
| [cce/adapter.py](../api/cce/adapter.py) | `parse_cce_canonical_vf_info` | 组合 CCE 解析及版本化 |
| [cce/value_versioning.py](../api/cce/value_versioning.py) | `ValueVersioningPass` | 从 CCE 私有 IR 建立独立 definition 和 loop-carried 关系 |
| [json/serialization.py](../api/json/serialization.py) | `canonical_vf_info_from_dict/to_dict` | Canonical JSON 对象编解码 |
| [programmatic/builder.py](../api/programmatic/builder.py) | `VfInfoBuilder` | 程序化显式构造 Canonical |

详细转换 pass 见 [API 说明](../api/README.md)。合法的 schema v1/v2 是 Canonical
协议版本；它们与已删除的旧 VFInfo 格式无关。

## Core 目录

```text
core/
├── simulator.py
├── execution/
│   ├── prepare.py
│   ├── expansion.py
│   ├── engine.py
│   ├── scheduler.py
│   ├── loop_control.py
│   └── membar.py
├── uarch/
│   ├── ifu.py
│   ├── idu.py
│   ├── registers.py
│   ├── register_bank.py
│   ├── shq.py
│   ├── exq.py
│   ├── exu.py
│   ├── lsu.py
│   └── address_state.py
├── common/
│   ├── instruction.py
│   ├── result.py
│   └── values.py
├── params/
│   ├── database.py
│   ├── profile.py
│   ├── timing.py
│   ├── compat.py
│   └── uarch.py
└── trace/
    ├── recorder.py
    ├── writer.py
    └── perfetto.py
```

各子目录的 `__init__.py` 仅标识 Python package，不提供兼容入口。

## 执行控制

| 文件 | 核心函数 | 职责 |
| --- | --- | --- |
| [simulator.py](../core/simulator.py) | `CoreVfCostModel.run_vf_info/predict_vf_cycles` | 检查输入类型、组装参数及组件、返回预测结果 |
| [prepare.py](../core/execution/prepare.py) | `CoreLoweringPass.lower`、`prepare_program` | 校验、当前执行能力检查、转换执行表示、分析静态布局 |
| [expansion.py](../core/execution/expansion.py) | `DynamicExpander.prepare_structured_stream` | 展开 loop/unroll、绑定动态 identity、标记最后使用 |
| [engine.py](../core/execution/engine.py) | `Engine.run/backend_step/admit` | 全局周期、传输、接收、完成事件及排空 |
| [scheduler.py](../core/execution/scheduler.py) | `enqueue_shq_to_exq/issue_exq_to_exu/issue_direct_from_shq` | 端口选择、FU FIFO、RR 和 EXU0 预留策略 |
| [loop_control.py](../core/execution/loop_control.py) | `LoopControl.can_dispatch/notify_dispatch` | VLOOP、block 和迭代开放时刻 |
| [membar.py](../core/execution/membar.py) | `accept_membar/update/blocks/notify_lsu_start` | 屏障接收、同步、释放、退休及后续 LSU 门控 |

动态展开在进入逐周期仿真前完成；IFU 不负责解析或展开。
默认动态指令上限为 20000，使用 `canonical_dynamic_instruction_limit` 调整。
未支持的显式 dependency、非 innermost unroll 等语义在执行准备阶段明确拒绝。

## 微架构组件及状态归属

| 组件 | 核心接口 | 独立维护的状态 |
| --- | --- | --- |
| [IFU](../core/uarch/ifu.py) | `next_inst/done` | 准备好的取指流 |
| [IDU](../core/uarch/idu.py) | `accept/dispatch` | 窗口、宽度门控、dispatch 和资源阻塞记录 |
| [Registers](../core/uarch/registers.py) | `rename/on_uop_start/on_uop_done/update_visibility` | 动态值映射、producer、consumer、generation、释放事件和可见 credit |
| [PhysicalRegisterBank](../core/uarch/register_bank.py) | `allocate/can_free/try_free` | vector/predicate 的容量、freelist 和释放安全条件 |
| [SHQ](../core/uarch/shq.py) | `enqueue/reserve/remove/schedule_shq_release` | 计算等待队列、compute/store 共用信用及延迟释放 |
| [EXQ](../core/uarch/exq.py) | `enqueue/pop/occupancy` | 每端口 ALU/SFU FIFO |
| [EXU](../core/uarch/exu.py) | `start/finish` | 执行中的 compute、II 历史和端口在途数量 |
| [LSU](../core/uarch/lsu.py) | `enqueue/update_ready/_issue_ready_lsu/finish` | LSQ、执行中的 memory、align generation、共享 UB slot/byte 预算 |
| [AddressStateTracker](../core/uarch/address_state.py) | `can_dispatch/notify_dispatch` | POST_UPDATE 地址状态的 IDU dispatch 就绪时间 |

组件通过明确的构造参数获得所需依赖。调度器选择流转和发射，组件修改自己拥有的
队列、端口和执行状态。循环与 membar 是执行控制逻辑；LSU 维护内存执行状态并
向 membar 报告开始和完成进度。

输入 value 名称没有分类语义：即使叫 `mem_result`，声明为 Register 就走寄存器路径；
即使叫 `v_buffer`，声明为 UB 就走存储对象路径。未声明的值直接报错。
动态值身份使用 `(definition_id, iteration_path)`，不生成名称 lane 后缀。

## 完成、释放和时序

Python 已删除 ROB 容量与按序退休。EXU、LSU 在指令完成时产生事件；Engine 的
`active_uops` 仅登记未完成指令，既不限制容量，也不按序 commit。

寄存器不等待 ROB 退休。释放需满足动态映射已结束、producer 非 pending、全部
consumer 已经过 start+offset 释放以及 generation 一致。SHQ 与 Registers 分别
维护实际资源和 IDU 可见信用，以保留跨周期反馈延迟。

Membar 自身的 retire 是屏障时序的一部分，仍影响最终 `vf_end_cycle`。
最终结果包括执行、屏障尾部和 drain；达到 max_cycles 而未排空会明确报错。

单周期顺序：可见 credit → 传输到达 → 取指 → membar 更新 → dispatch →
寄存器/SHQ 释放 → EXU/LSU 完成 → LSU 第一轮仲裁 → compute → LSU 剩余预算。
这套顺序保留原模型的 forwarding、II、资源反馈和 barrier 一周期边界。

## 参数与日志

`params/database.py::ParamDB` 加载 ISA、forwarding、II 和 uarch；`resolve_inst`
建立不可变 `InstructionProfile`，后续查询复用缓存。`timing.py` 提供时序和端口查询；
`compat.py` 保留现有参数匹配规则。配置文件及其兼容规则尚未进入本轮重构。

`trace/recorder.py` 保存 start/done/blocked 事件，`trace/writer.py` 集中写文件：
`start_by_cycle.json`、`done_by_cycle.json`、`sim_history.json`、`membar_history.json`、
IDU dispatch/blocked、VLOOP trace 和 Perfetto `trace.json`。
移除 ROB 后不再输出指令 retire 事件或 `preg_old`；屏障 retire 记录仍保留。
`model_warnings.json` 记录参数 fallback，旧空 `vreg_capacity_warnings` 字段已删除。

## 验证

```bash
python3 -m unittest discover -s tests -q
python3 tools/run_cost_model_regression.py --tier smoke
```

Core 重构针对 Canonical loop/unroll、谓词/向量寄存器、align state、POST_UPDATE、
共享 UB 预算和 membar 保留时序测试，同时检查改名后的相同 Canonical 程序产生
一致的周期及物理寄存器调度。Native 环境未配置时，跨语言测试会跳过。
