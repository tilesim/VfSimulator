# VfSimulator 代码结构与关键函数导览

更新日期：2026-09-17。核对版本：`master`，提交 `d9b19f3` 及本次命名/警告清理改动。

本文说明当前代码实际执行关系，而不是未来重构计划。文件链接均相对仓库路径，适合本地及远程仓阅读。

## 1. 模型范围与分支边界

VfSimulator 根据指令依赖、时序参数、队列和资源约束预测一个 VF 的执行周期。
它不是数值执行器：预测完成不代表算子的数值精度通过，数值验证需要 CAmodel/硬件及 golden 对照。

- 正式输入合同为 `CanonicalVfInfo`；CCE 和 Canonical JSON 是不同的输入方式，最终进入相同合同。
- Python 和 Native C++ 都有执行核心，并非 Python 当前已经完全调用 C++。
- 本分支使用 `configs/` 根目录的一套参数，没有 MultiSoC 分支的 `--soc` 选择和 DV100/DV121 参数目录。
- 本分支实现寄存器依赖、align state 依赖及方向性全局 membar，不自动根据 UB 地址区间建立局部依赖。
- `main.py` 中的 `--three-ports` 是旧实验配置入口，不等于 MultiSoC 分支的 A6 后端。
- 文件仍存在不代表它属于正式执行路径：旧 VFInfo、旧寄存器规范化和部分 optimizer 工具用于迁移、历史实验或专项测试。

## 2. 顶层目录

| 路径 | 职责 |
|---|---|
| [main.py](../main.py) | Python 命令行入口，读取输入、组装模型、运行和输出日志 |
| [api/](../api/) | 公共预测 API、CCE 解析、Canonical 数据合同与校验 |
| [api/frontend/](../api/frontend/) | Python schema、builder、Catalog、版本化、Core lowering |
| [api/native/](../api/native/) | C++ Canonical 合同、validator、JSON 输入及生成的配置接口 |
| [core/](../core/) | Python IFU、IDU、OoO、ISU、LSU、寄存器和控制单元 |
| [native/](../native/) | C++ 执行模型、CMake 目标及 Native 测试 |
| [configs/](../configs/) | ISA、forwarding、II、微架构、语义 Catalog 和 override 类型约束 |
| [tests/](../tests/) | Python 单元测试、共享 Canonical fixtures、跨语言测试 |
| [regression_suite/](../regression_suite/) | 稳定回归输入、case 清单、周期基线及精度报告 |
| [VFtest/](../VFtest/)、[cce_code/](../cce_code/) | 测试输入与算子 CCE/DSL 样例，不是调度实现 |
| [tools/](../tools/) | 回归驱动、参数提取、生成代码、日志分析与绘图 |
| [ascend_runner/](../ascend_runner/) | CAmodel/设备侧实验及单指令、forwarding、II 参数测量辅助 |
| [optimizer/](../optimizer/) | DAG、切分、unroll 等搜索实验，不是单次预测的必经步骤 |
| [skills/](../skills/)、[codex_optimization_skill/](../codex_optimization_skill/) | 算子优化流程和配套工具 |
| [docs/](./)、[VF_modeling.md](../VF_modeling.md) | 设计说明、建模规则与历史记录，具体行为以代码及测试为准 |
| `results/`、`build-native/` | 运行结果和构建产物；不能作为新 clone 必然具备的源码依赖 |

## 3. 一次预测的完整调用链

### 3.1 Python

```text
CCE / DSL                         Canonical JSON           程序化构造
InputAPI.load_cce()               InputAPI.load_json()      VfInfoBuilder
  CCE scope parser                  JSON Schema 校验          build()
  AdapterProgram                    对象解码
  ValueVersioningPass
                 \                    |                    /
                          CanonicalVfInfo
                          通用及 Catalog 语义校验
                          Core 兼容性检查
                          CoreLoweringPass.lower()
                                    |
                       内部 program / values / params / uarch
                                    |
                   ProgramAnalyzer + Flattener + ParamDB
                                    |
                   IFUUnroll -> IDU -> OoOCoreMainline
                                    |
                         run_simulation() 逐周期推进
                                    |
                     vf_end_cycle / 日志 / Perfetto trace
```

`main.py` 与 `CoreVfCostModel` 分别组装命令行及库调用，两者复用 lowering 和 Core，
不要误以为 `main()` 一定通过 `CoreVfCostModel.run_vf_info()` 转发。

### 3.2 Native C++

```text
CanonicalVfInfo / loadCanonicalJsonVfInfo()
  -> runCanonicalVfInfo(vfInfo, db, ...)
  -> validateCanonicalVfInfo()
  -> lowerCanonicalProgram() + resolveCanonicalUarch()
  -> CanonicalRuntimeProgram（动态指令、values、loop/block 信息）
  -> IFU -> IDU -> OoOCoreMainline
  -> runSimulation()
  -> SimulationResult
```

Python 的动态展开主要在 `core/ifu.py`；Native 的 Canonical 动态展开主要在
`native/CanonicalProgramLowering.cpp`，`native/IFU.cpp` 负责取出已展开指令。
因此两端文件不是严格的一一翻译关系。

## 4. 公共入口与前端

### 4.1 输入和预测接口

| 文件 | 重要函数/类 | 功能 |
|---|---|---|
| [main.py](../main.py) | `build_arg_parser()`、`load_input_canonical_vf_info()` | 解析 CLI，选择 CCE 或 Canonical JSON |
| 同上 | `build_uarch()`、`main()` | 合并配置及实验开关，初始化组件并运行 |
| [api/input_api.py](../api/input_api.py) | `InputAPI.load_cce()`、`load_json()`、`new_builder()` | 统一输入入口，返回或构造 Canonical |
| [api/vf_costmodel.py](../api/vf_costmodel.py) | `VfCostModel.predict_vf_cycles()` | 公共预测抽象，输入 `CanonicalVfInfo` |
| [api/simulator_costmodel.py](../api/simulator_costmodel.py) | `CoreVfCostModel.run_vf_info()` | 先校验，再兼容性检查及 lowering，返回结果字典 |
| 同上 | `predict_vf_cycles()`、`run_payload()` | 分别返回周期，或解码并运行 Canonical JSON 对象 |
| 同上 | `_run_lowered_payload()` | 内部模型组装入口，要求 `canonical_input=True`，不是外部输入合同 |
| 同上 | `predict_cce_file_cycles()` | CCE 到周期的便捷封装 |

### 4.2 Canonical 合同

[schema.py](../api/frontend/schema.py) 中的重要结构：

| 类型 | 含义 |
|---|---|
| `CanonicalVfInfo` | values、storage objects、context、params、uarch 和来源信息 |
| `CanonicalStorageObject` | 稳定存储对象身份，区别于一次值定义 |
| `CanonicalValue` | 一次值定义，记录 logical ID、producer、storage、dtype 等 |
| `CanonicalOperand` | 指令输入/输出对 value 的引用及角色、访存描述 |
| `CanonicalInstruction` | opcode、form、class、输入输出、属性和源码位置 |
| `CanonicalLoop`、`LoopCarriedValue` | 循环、迭代变量及 entry/back-edge/exit 关系 |
| `CanonicalMembar` | 独立控制节点，不伪装成普通计算或 LSU 指令 |
| `MemoryAccess`、`AffineExpression` | 内存对象及结构化偏移；能表达不等于主线 Core 自动做地址依赖 |
| `DependencyRef` | 显式 memory/control ordering 的合同表达；当前 Core 不执行此类额外依赖 |

- [builder.py](../api/frontend/builder.py)：`register_storage_object()`、`register_value()`、`add_instruction()`、`add_membar()`、`loop()`、`build()`，供程序化构造。
- [validator.py](../api/frontend/validator.py)：`validate_canonical_vf_info()`，检查类型、ID、producer/output、作用域、loop-carried、class/access 及 Catalog 语义。
- [json_adapter.py](../api/frontend/json_adapter.py)：`CanonicalJsonVfInfoAdapter.load()/from_payload()`，先按共享 JSON Schema 严格校验 raw payload，避免拼错字段被静默丢弃。
- [serialization.py](../api/frontend/serialization.py)：`canonical_vf_info_to_dict()/canonical_vf_info_from_dict()`，负责序列化转换；外部不可信 JSON 应走 adapter 完整校验。
- [uarch_validation.py](../api/frontend/uarch_validation.py)：`validate_uarch_overrides()`，检查配置类型、目标支持范围和废弃字段。

### 4.3 CCE 解析与指令语义

| 文件 | 重要函数 | 功能 |
|---|---|---|
| [cce_adapter.py](../api/cce_adapter.py) | `extract_cce_vf_scopes()`、`list_cce_vf_kernels()` | 查找 `__VEC_SCOPE__` 及可选 VF 内核 |
| 同上 | `parse_cce_canonical_vf_info()` | CCE 正式 Canonical 入口 |
| 同上 | `_VFScopeParser._parse_block()`、`_parse_statement()` | 解析 block、循环、声明、指令、alias、membar，维护符号作用域 |
| 同上 | `_bind_catalog_call()`、`_bind_catalog_argument()` | 按签名绑定操作数及配置参数 |
| 同上 | `_memory_accesses_for_call()`、`_parse_ub_reference()` | 提取访存及 UB 引用；不在这里执行硬件调度 |
| [instruction_catalog.py](../api/frontend/instruction_catalog.py) | `InstructionCatalog.lookup()`、`resolve_and_validate_form()` | 统一 opcode、签名、form 和 specialization 语义 |
| 同上 | `compare_timing_config()` | 检查语义 Catalog 与时序覆盖差异 |
| [value_versioning.py](../api/frontend/value_versioning.py) | `ValueVersioningPass.run()` | Adapter IR 到 Canonical definition 版本化 |
| 同上 | `_version_instruction()`、`_version_loop()` | 处理 producer、alias 快照和循环 carried/exit 值 |

CCE 解析器使用 [adapter_ir.py](../api/frontend/adapter_ir.py) 作为前端内部表示。
它不是旧公共 VFInfo 正式预测入口。解析范围以受支持的 VF 语句为限，不是通用 C++ 编译器，
也不模拟 host 侧代码、DMA 和完整标量控制程序。

### 4.4 Core lowering

[core_lowering.py](../api/frontend/core_lowering.py) 的核心函数：

- `ensure_current_core_compatible()`：区分“schema 合法”和“当前 Core 可执行”；拒绝未实现的显式 dependencies、非最内层 unroll 等情况。
- `lower()`：生成内部 payload，并保留 Canonical 标识。
- `_lower_node()`：转换普通指令、循环和 membar。
- `_memory_accesses()`：传递访存描述，不意味着在当前 master 建立 UB RAW/WAR 地址依赖。

## 5. Python Core

### 5.1 程序分析、IFU 和 IDU

| 文件 | 重要函数 | 功能 |
|---|---|---|
| [program_analysis.py](../core/program_analysis.py) | `ProgramAnalyzer.infer_top_block_loop_bounds()` | 提取顶层 block 与 loop bounds，供 IDU 循环时序使用 |
| [flatten.py](../core/flatten.py) | `Flattener.flatten()` | 将结构化程序转为含循环边界的线性表示，不等于全部动态迭代已经展开 |
| [ifu.py](../core/ifu.py) | `IFUUnroll.next_inst()`、`_next_inst_raw()` | 按动态顺序输出指令和控制节点 |
| 同上 | `_create_loop_carried_bindings()`、`_advance_loop_carried_bindings()`、`_complete_loop_value_aliases()` | 管理每层循环进入、回边和退出时的动态值绑定 |
| 同上 | `_build_pending_unrolled_structured()` | Canonical 最内层循环 unroll 展开 |
| 同上 | `prepare_structured_stream()`、`_annotate_structured_value_lifetimes()` | 预展开动态流，统计值实例最后使用，生成释放标记 |
| 同上 | `empty_top_block_ids()` | 识别空块，避免零次循环阻塞后续 block |
| [idu.py](../core/idu.py) | `IDU.accept()`、`dispatch()` | 维护取指窗口，按宽度、循环开放时刻及后端 credit 分发 |
| 同上 | `_trigger_next_vloops()`、`_next_nonempty_top_block()` | 推进循环/block，跳过空块 |
| [value_storage.py](../core/value_storage.py) | `ValueStorageLookup.is_register()/is_ub()/is_scalar()` | 按 values 元数据判断存储类别，不依赖 Canonical value 名称前缀 |

动态身份需要同时关注 `static_instruction_id`、`iteration_path`、`stream_seq` 和 `inst_id`。
同一静态指令在多轮循环中出现，不代表同一次动态定义。

当前 Canonical IFU 会预展开动态流，并受 `canonical_dynamic_instruction_limit` 限制；
不能把它描述成完全流式、常量内存的展开器。

### 5.2 OoO 核心、寄存器和生命周期

[ooo.py](../core/ooo.py) 定义 `Uop` 和 `OoOCore` 公共状态及辅助方法；
[ooo_mainline.py](../core/ooo_mainline.py) 的 `OoOCoreMainline` 是实际执行实现。
[ooo_factory.py](../core/ooo_factory.py) 的 `create_ooo_core()` 只创建该主线后端，
历史 `queue_level4` 名称不代表当前仍有四套并列后端。

| 类/函数 | 关键作用 |
|---|---|
| `RenameController.accept()` | 分配物理寄存器、查询/更新 RAT、绑定源 producer、Profile 和 align state，进入 SHQ/LSQ/ROB |
| `PregLifecycleController.schedule_src_release_from_start()` | 按 consumer start 加配置偏移安排源引用释放，默认偏移 4 cycles |
| `PregLifecycleController.can_free_preg()/try_free_preg()` | 检查映射、consumer、pending、可释放时刻，并清理 producer 元数据和归还 credit |
| `SHQResourceController.schedule_shq_release()/update_idu_visibility()` | 管理队列 credit 的释放及 IDU 可见延迟 |
| `OoOCore._ready_time_for_src()`、`_compute_ready_cycle()` | 根据 producer 和 forwarding 计算寄存器依赖满足时间 |
| `OoOCore._eligible_exu_ports()` | 按指令 dispatch 约束选择合法 EXU |
| `OoOCore._store_ready_cycle()` | 普通 STORE 数据依赖及状态型 STORE 的就绪判断 |
| `OoOCoreMainline.step()` | 完成/退休、释放资源、更新 ready、LSU 仲裁、SHQ→EXQ→EXU 发射 |
| `OoOCore.vf_end_cycle()` | 计算完整 VF 结束周期，不能以最后一条 compute done 直接代替 |

Canonical 的最后使用标记会让 rename 在最后一个动态引用绑定时去掉不再需要的 RAT 映射。
实际 preg 回收仍需满足 consumer 释放及其他安全条件，不能简化为“看到 last use 立即释放”，
也不能理解为“所有值必须等下一次写同名逻辑寄存器才释放”。

### 5.3 ISU：计算指令分发与执行

[isu.py](../core/isu.py) 的 `ISUController` 负责计算指令路径：

```text
SHQ（计算指令等待依赖）
  -> EXQ0/EXQ1（每端口 ALU/SFU 子队列）
  -> EXU0/EXU1
```

- `enqueue_shq_to_exq()`：从 SHQ 选择 ready 指令，检查合法端口和队列容量。
- `select_fu_rr_port()`：ALU/SFU 各自 RR 指针选择端口。
- `_exu0_only_pressure_count()`、`_apply_exu0_reserve()`：前看 SHQ 中受限指令压力，执行 EXU0 平衡预留。
- `issue_exq_to_exu()`：检查子队列队头、接收延迟、II、inflight cap 后开始执行。同 FU FIFO，ALU/SFU 之间可乱序。
- `predict_exq_issue_cycle()`：估计队列端口可执行时间，供对应策略使用。
- `issue_direct_from_shq()`：不启用分级队列模型时的实验路径，不是默认配置。

当前默认 `fu_round_robin_exu0_reserve`：lookahead=8、min_count=1、每端口 inflight cap=7。
lookahead 的压力统计不是仅看 ready 指令。EXU0_ONLY 始终受合法端口约束。

### 5.4 LSU 与 align state

- LOAD/STORE 进入 LSQ，不经过计算指令的 EXQ 分发路径。
- `OoOCoreMainline._lsu_issue_key()`：物理寄存器余量低于阈值时 STORE 优先，否则 LOAD 优先；同类按动态年龄排序。
- `_issue_ready_lsu()`：同时受 load/store 端口、共享 `ub_slots`、依赖和 membar gate 约束。
- 当前默认最多 2 LOAD、1 STORE，且共享 `ub_slots=2`，所以不是每周期能同时发 2 LOAD+1 STORE。
- 一个周期内 compute 前后都会尝试 LSU 仲裁，但共享已用预算及 membar 阻塞日志去重集合，不是预算翻倍。
- `OoOCore.bind_align_state()`：VSTUS 向当前 generation 追加 producer，VSTAS 入队时封存该组 producer 快照。
- `AlignGeneration`、`AlignProducerRecord`：表达多 producer 收集状态，不进入普通 RAT，也不分配 preg。
- VSTAS 等待所属组 VSTUS 的 start 加状态 forwarding，不是等待所有未来 VSTUS，也不伪造普通寄存器 producer。

### 5.5 Membar 与逐周期主循环

[control_unit.py](../core/control_unit.py) 的 `ControlUnit` 提供方向性全局阻塞；
[membar_timing.py](../core/membar_timing.py) 的 `TimedControlUnit` 增加发射、同步释放、退休及消费者延迟。

| 函数 | 功能 |
|---|---|
| `accept_membar()` | 按动态顺序登记 barrier，并在 timed 模型中封存前段 LSU 信息 |
| `observe_instruction()`、`notify_lsu_start()` | 记录指令段及 LSU 实际开始时刻反馈 |
| `update()` | 推进 barrier 发射、指定方向前序完成检查、同步释放及退休 |
| `blocks()` | 判断后续 LSU 是否被对应方向 barrier 阻塞 |

有 `uarch.membar_timing` 字段时必须严格校验并使用 timed 模型；字段完全缺失才用兼容模型。
`VST_VLD` 等前序 STORE，阻塞后续 LOAD；`VLD_VST` 方向相反。
barrier 不进入 IDU/SHQ/EXQ/EXU，不直接阻塞无关 compute，但占用控制时序并影响 VF 结束。
具体参数与测量近似见 [membar_timing_model.md](membar_timing_model.md)。

[simulator_runner.py](../core/simulator_runner.py) 的 `run_simulation()` 每周期大致依次：

1. 更新 IDU 可见 credit，交付 IDU→OoO 延迟管线。
2. IFU 补充 IDU 窗口，membar 直接交控制单元。
3. 更新控制单元；查询前序 LSU 时覆盖 IDU、传输管线和 OoO。
4. IDU 分发，并登记传输延迟及资源预约。
5. `ooo.step()` 处理本周期完成、退休和新发射。
6. 合并 barrier 退休时刻，检查所有组件是否排空。

此顺序是建模语义的一部分。把控制单元检查随意挪到完成更新之后，可能导致一周期差异。
超过 `max_cycles` 未排空会报错，而不是返回一个有效预测。

## 6. 参数与缓存

| 文件 | 含义 |
|---|---|
| [configs/isa.json](../configs/isa.json) | opcode/form 的 latency、class、FU、dispatch 等 |
| [configs/forwarding.json](../configs/forwarding.json) | producer→consumer 数据可用延迟，含计算与 LSU 之间的链路 |
| [configs/InitiationInterval.json](../configs/InitiationInterval.json) | previous→current 的发射间隔，与数据依赖 forwarding 不同 |
| [configs/uarch.json](../configs/uarch.json) | 队列、端口、preg、分发策略、管线延迟和 membar 时序 |
| [configs/instruction_catalog.json](../configs/instruction_catalog.json) | 语义支持及参数签名；不应通过缺少实测 timing 来判定语义不支持 |
| [configs/uarch_override_schema.json](../configs/uarch_override_schema.json) | 公共 override 字段的类型、目标范围等约束；不是第二套硬件参数 |

[param_db.py](../core/param_db.py) 的重点函数：

- `_prepare_inst_param_templates()`：加载期准备已合并 form 参数。
- `get_inst_form()`：公共参数查询；兼容候选规则见 [param_compat.py](../core/param_compat.py)。
- `resolve_inst()`：按 opcode、requested form、dtype 得到不可变 `InstructionProfile`，供 Uop 重用。
- `get_forwarding_for_profiles()`、`get_ii_for_profiles()`：静态指令对缓存，不缓存动态 ready 时刻。
- `get_warnings()`：记录缺失参数和 fallback，避免把回退值误当成实测值。

Profile ID 和缓存属于 ParamDB 实例，不能跨数据库混用。
[isa_traits.py](../core/isa_traits.py) 对外提供指令类别等查询，[instruction_profile.py](../core/instruction_profile.py) 定义 Profile。
Native 对应 `ParamDB::inst()`、`forwardingCycles()`、`initiationInterval()`。

## 7. Native 对应关系及构建边界

| C++ 文件 | 职责/关键入口 |
|---|---|
| [api/native/CanonicalVfInfo.h](../api/native/CanonicalVfInfo.h)、`.cpp` | Canonical 结构与 `validateCanonicalVfInfo()` |
| [api/native/CanonicalJsonVfInfoAdapter.cpp](../api/native/CanonicalJsonVfInfoAdapter.cpp) | `loadCanonicalJsonVfInfo()` |
| [native/CanonicalProgramLowering.cpp](../native/CanonicalProgramLowering.cpp) | `lowerCanonicalProgram()`、`resolveCanonicalUarch()` |
| [native/SimulatorRunner.cpp](../native/SimulatorRunner.cpp) | `runCanonicalVfInfo()`、`runSimulation()` |
| [native/IFU.cpp](../native/IFU.cpp)、[IDU.cpp](../native/IDU.cpp) | `nextInst()`、`dispatch()`，动态取指和 credit/loop gating |
| [native/OOO.cpp](../native/OOO.cpp) | `OoOCoreMainline::accept()/step()`，rename、ISU、LSU、寄存器生命周期集中在此 |
| [native/ControlUnit.cpp](../native/ControlUnit.cpp) | `acceptMembar()`、`update()`、`blocks()`，包含 timed membar |
| [native/ParamDB.cpp](../native/ParamDB.cpp)、[ParamSchema.h](../native/ParamSchema.h) | 参数解析、fallback 和配置类型 |
| [native/VfsimNativeJsonRunner.cpp](../native/VfsimNativeJsonRunner.cpp) | Native JSON 命令行运行器，供回归调用 |

[native/CMakeLists.txt](../native/CMakeLists.txt) 的主要目标：

- `vfsim::native_core`：正式 Canonical 预测库。
- `vfsim::native_legacy`：由 `VFSIM_BUILD_LEGACY_MIGRATION` 控制的独立旧格式迁移库。
- `vfsim::ir_planner`：由 `VFSIM_ENABLE_MLIR_PLANNER` 控制的可选 MLIR 接口，不是默认核心依赖。
- 测试及 JSON runner 受构建选项控制；当前 Native 测试集要求同时启用 legacy migration。

增加指令签名或 override 字段时，还应核对 `tools/generate_instruction_catalog_cpp.py`、
`tools/generate_uarch_override_schema_cpp.py` 生成的 C++ 表，避免两端合同漂移。

## 8. 日志、测试和问题定位

### 8.1 标准输出

| 输出 | 用途 |
|---|---|
| `vf_end_cycle` | 完整 VF 的预测周期 |
| `cycles_executed` | 主循环推进计数，与 VF 时间不是同一个字段 |
| `sim_history.json` | 详细调度事件，分析 rename、队列、发射和退休 |
| `start_by_cycle.json`、`done_by_cycle.json` | 逐行 JSON 事件日志；后缀虽为 `.json`，不要按单个 JSON 数组读取 |
| `idu_to_ooo.json` | IDU 分发和资源状态，定位 preg/队列及循环门控 |
| `vloop_trace.json` | 循环开放及推进时序 |
| `membar_history.json` | timed 模型的 issue、sync_release、retire 事件 |
| `trace.json` | [perfetto_trace.py](../core/perfetto_trace.py) 的 `build_perfetto_trace()/dump_perfetto_trace()` 生成的可视化文件 |

参数警告由 `dump_model_warnings()` 输出。具体事件字段以产生代码为准。
计算完成 IPC 应筛选 compute done，不能将 load/store、membar 或 start 事件混算；
滑动窗口、时间对齐方式与 VF 总周期应分别标注。

### 8.2 推荐排查入口

| 问题 | 先查看 |
|---|---|
| CCE 参数、form、predicate 报错 | CCE binder → instruction Catalog → validator 的 source location |
| 循环依赖断链或读错 producer | ValueVersioning → carried bindings → iteration_path → RAT/preg 源 |
| IDU 长时间停住 | `IDU.dispatch()`、credit、top-block/loop gate、空循环处理 |
| preg 用尽 | IFU lifetime 标记、RenameController、PregLifecycleController、IDU 可见 credit |
| EXU 负载不均或不能发射 | 合法端口、reserve/RR、EXQ 子队列头、II 和 inflight cap |
| store 堆积 | 普通数据/align-state ready、阈值仲裁、UB slots、membar gate |
| 多 membar 时序不准 | barrier issue/release/retire 与中间 LSU start，不只看 LSU done |
| Python/Native 结果不同 | 相同 Canonical、相同配置、动态序列，再比较逐事件日志 |

### 8.3 常用验证命令

以下命令在仓库根目录执行。2026-09-17 默认精度命名及旧 warning 清理后，已执行测试与全量回归。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
cmake -S native -B build-native -DVFSIM_BUILD_TESTS=ON -DVFSIM_BUILD_LEGACY_MIGRATION=ON
cmake --build build-native -j2
ctest --test-dir build-native --output-on-failure
python3 tools/run_cost_model_regression.py --tier smoke
python3 tools/run_native_cost_model_regression.py --tier smoke
```

主线回归基线为 `regression_suite/cases/baseline_canonical_membar.json`。
单测通过、回归容差通过、与旧版本逐 case 周期完全相等、CAmodel 数值精度通过是四种不同结论，不能混用。

## 9. 历史和辅助代码应如何理解

- `api/vf_info.py`、`api/json_adapter.py`、`api/frontend/legacy_vf_info_adapter.py`：旧格式迁移；独立工具入口为 `tools/convert_legacy_vfinfo.py`。
- `core/vreg_live_range_normalization.py`、`core/program_canonicalization.py`：历史变换与专项测试，不是当前 Canonical 主线固定必经 pass。
- Native `Program*` 旧变换和 `api/native/VfInfo.*` 放在 legacy migration 目标，而非正式核心库。
- `optimizer/dag_lib.py` 的 `OperatorDAG.from_inst_list()`、`critical_path_length()` 提供分析；`partitioner.py` 的 `Partitioner` 生成切分方案；`SplitOnlyOptimizer.evaluate()/optimize()` 等搜索候选。部分工具沿用历史 trace 合同，使用前需核对其输入输出与调用路径，不能直接当作新的 Canonical API 示例。
- `results/` 中的 IPC 图、CAmodel 日志及临时实验脚本不改变主线默认配置，也不代表当前分支支持生成这些结果的全部实验能力。

建议初次阅读顺序：`api/simulator_costmodel.py` → `schema.py`/`core_lowering.py` →
`core/simulator_runner.py` → `ifu.py`/`idu.py` → `ooo_mainline.py`/`isu.py` →
`param_db.py`/`membar_timing.py`；之后再对照 Native。

## 10. 清理记录与遗留项

### 默认精度 dtype 命名容易被误认为 VF 全局精度

- 记录日期：2026-09-17；状态：已清理。
- CLI、CoreLoweringPass 内部 payload、Python API 默认值及 Core 内部默认状态统一命名为
  `fallback_dtype`；Native runtime 字段及 IDU/OoO 默认状态使用 `fallbackDtype` 命名。
- 当前含义：这是后端参数查询的默认/兜底精度，不表示整个 VF 的指令都具有相同精度。
  指令查询优先使用自身 `form`；操作数的 `dtype` 单独保存在值与操作数描述中。
  同一个 VF 可包含 FP32 计算、FP32→FP16 转换及 FP16 写出。
- 兼容边界：`CoreVfCostModel(dtype=...)` 保留为旧关键字，新调用使用 `fallback_dtype=...`；
  底层组件构造函数及 ParamDB 的既有 `dtype=` 查询关键字保留，内部不再称其为 VF 全局精度。
  Canonical JSON 没有新增全局精度字段，操作数 dtype 和指令 form 不变。
- 验收：仅改善命名与说明，不改变参数选择优先级、fallback 行为和预测周期；
  使用混合精度用例及回归测试确认行为不变。

### 静态寄存器容量 warning 判据不合理

- 记录日期：2026-09-17；状态：旧 warning 已删除，架构寄存器预警按用户要求留待后续。
- 已删除 Python `ProgramAnalyzer.collect_vreg_capacity_warnings()`、CLI 调用和
  Native 旧迁移辅助类的对应实现，不再产生 `vreg_namespace_overflow_risk`。
  日志中的 `vreg_capacity_warnings` 键为兼容现有读取者保留空列表；时序 fallback warning 不变。
- 原判据：循环中寄存器值名称的去重数量乘以 unroll，与物理寄存器数量比较。
  该比较混淆了值定义数量、架构寄存器需求及动态物理寄存器压力，没有考虑生命周期复用，
  不能合理判定寄存器溢出，也不能据此降低预测可信度。
- 后续方向：另行设计架构寄存器建模或压力感知，再补充架构寄存器溢出风险 warning。
  本次没有设置架构寄存器容量，没有实现新预警，也没有改变物理寄存器分配/释放行为。
  新分析应基于展开后的值活跃区间及目标架构寄存器容量，考虑循环回边与跨循环存活值，
  不再简单采用“名称数量 × unroll”，也不能把物理寄存器数量当作架构容量。
- 语义边界：架构寄存器压力分析只提示编译器可能插入 spill，是否实际发生仍需编译结果验证；
  物理寄存器不足由后端 rename/credit 机制模拟，可报告实际阻塞周期，不作为编译器 spill 判据。
- 验收：生命周期不重叠的大量定义不误报；同时存活值超过目标架构容量时给出明确风险提示；
  移除旧 warning 不改变调度和周期结果，新 warning 不自动向程序插入 spill 指令。

### 本次清理验证

- Python 单元测试：201 项全部通过（设置 `VFSIM_NATIVE_RUNNER` 后包含跨语言测试，无跳过）。
- Native：全新构建成功，6 项 CTest 全部通过。
- Python full regression：25 个 case，与修改前 `current_metrics.json` 的全部 case 指标严格相等。
- Native full regression：25 个 case 通过，`vf_end` 与 Python 逐项严格相等。
- `compileall` 和 `git diff --check` 通过；未更新周期基线、未修改时序配置。
- 架构寄存器建模与新 warning 不在本次验收范围，仍是后续开发事项。
