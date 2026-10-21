# 配置参数

`configs/` 保存指令语义、微架构资源和时序数据。
`core/params/database.py::ParamDB` 提供参数查询，运行组件读取结构化
`UarchParameters` 和 `InstructionProfile`。

## 配置文件

| 文件 | 内容 | 使用位置 |
| --- | --- | --- |
| [instruction_catalog.json](../configs/instruction_catalog.json) | opcode、签名、operand、form、mode、地址语义、UB 预算和对齐操作 | API 参数绑定与指令校验、Core 入队 |
| [uarch.json](../configs/uarch.json) | 资源容量、传输延迟、循环、调度和屏障参数 | `loader.py`、`uarch.py`、执行组件 |
| [uarch_override_schema.json](../configs/uarch_override_schema.json) | Canonical 标量覆盖的字段类型及 Python/C++ 支持范围 | API 配置校验和共享表生成 |
| [isa.json](../configs/isa.json) | 指令 form 的 latency、FU、端口及 VF startup/drain | 指令 Profile 解析 |
| [forwarding.json](../configs/forwarding.json) | producer 到 consumer 的可用间隔 | 寄存器和对齐状态就绪查询 |
| [InitiationInterval.json](../configs/InitiationInterval.json) | 前后指令在同端口的发射间隔 | EXU 发射仲裁 |

Catalog 提供语义声明，ISA、forwarding 和 II 提供执行时序。Catalog 的语义支持范围和
参数表的测量覆盖范围分别检查。`InstructionCatalog.compare_timing_config()` 返回
两份声明之间的差异供工具检查。

## 加载与覆盖

`loader.resolve_path()` 按以下顺序选择配置：显式路径、环境变量、默认候选文件。
四类环境变量为 `ISA_JSON_PATH`、`UARCH_JSON_PATH`、`FORWARDING_JSON_PATH` 和
`II_JSON_PATH`。默认文件位于 `configs/`，loader 也检查根目录候选路径。
ISA 与 uarch 文件必须存在；可选 forwarding/II 文件缺失时使用查询回退规则。
格式错误、字段类型错误和无效的表格单元会抛出异常。

`CoreVfCostModel` 创建时加载一次参数快照，连续调用 `run_vf_info()` 复用该快照。
配置文件或路径环境变量的更新在创建新的模型实例后生效。
每次运行创建独立的 `ParamDB`、查询缓存、警告记录和微架构状态；
VF 的 `uarch` 覆盖只作用于本次运行。forwarding 查询结果按 producer/consumer
Profile 对缓存，缓存值为相对周期数。

`resolve_uarch(db.base_uarch, vf_info.uarch)` 在已解析的基础配置上应用 VF 覆盖。
基础配置字段来自 `UarchParameters`；未知字段报错。Canonical 的 `uarch` 只接收
合同声明的标量字段，`membar_timing` 作为对象配置放在基础 `uarch.json`。
基础配置中未声明的字段使用 `core/params/types.py` 的 dataclass 默认值。

## 默认资源与调度

下表取自仓库的 `configs/uarch.json`，容量使用各组件的计数单位。

| 参数 | 配置值 | 作用 |
| --- | ---: | --- |
| `issue_ports` | 2 | EXU 端口数，模型支持 1 或 2 |
| `load_ports` / `store_ports` | 2 / 1 | 每周期 LOAD / STORE 发射数 |
| `ub_slots` | 2 | LOAD/STORE 共用的每周期发射槽 |
| `ub_bandwidth_bytes_per_cycle` | 512 | 每周期共享 UB 传输预算，单位 B |
| `IDU_window_width` / `IDU_issue_width` | 6 / 5 | IDU 窗口容量和每周期分发数 |
| `LDQ_width` | 2400000 | LSU 等待队列容量 |
| `vreg_num` / `physical_predicate_registers` | 68 / 32 | vector / predicate 物理资源池容量 |
| `shq_depth` / `exq_depth` | 58 / 26 | SHQ 深度和每端口 EXQ 深度 |
| `exq_issue_inflight_cap_per_port` | 7 | 每端口在执行计算指令的上限，0 表示关闭限制 |
| `canonical_dynamic_instruction_limit` | 20000 | 展开后的动态指令上限，0 表示关闭限制 |
| `shq_to_exq_port_per_cycle` | 1 | 每个端口每周期 SHQ→EXQ 分发额度 |
| `consumer_release_start_offset` | 4 | 消费者开始到源引用释放的延迟 |
| `lsu_store_priority_preg_threshold` | 1 | 可用 vector 资源低于此值时优先考虑 STORE |

`enable_isu_queue_model=true` 使用 SHQ→EXQ→EXU 路径。
`shq_exq_dispatch_policy=fu_round_robin_exu0_reserve` 使用 FU 轮转及 EXU0 预留；
`exu0_reserve_lookahead=8`、`exu0_reserve_min_count=1` 控制预留扫描。
`enable_shq_credit_model=true` 启用 compute/store 共用信用。
`exq_capacity_counts_inflight=false` 使用队列占用进行 EXQ 容量核算。

`enable_cross_fu_ii` 控制跨 FU 的成对 II；`enforce_same_cycle_src_hazard` 控制同周期
源冲突检查。`enable_exq_greedy_balance`、`predict_exq_issue_with_cross_fu` 和
`shq_exq_static_rr` 提供额外端口选择策略；其默认值在 `UarchParameters` 中均为 false。
`assert_start_release_integrity` 默认 true，检查源释放事件的完整性。

## 传输和信用延迟

| 参数 | 配置值 | 作用 |
| --- | ---: | --- |
| `idu_to_ooo_delay` | 1 | IDU 分发到后端接收的传输延迟 |
| `ooo_to_shq_delay` / `ooo_to_lsq_delay` | 1 / 1 | 后端接收后的计算/访存入队就绪延迟 |
| `exq_recv_delay` | 1 | EXQ 接收到可发射的延迟 |
| `shq_release_delay` | 1 | SHQ 信用释放延迟 |
| `idu_visible_preg_delay` / `idu_visible_shq_delay` | 0 / 0 | 释放资源反馈给 IDU 的可见延迟 |
| `idu_post_update_ready_latency` | 1 | IDU 发出指针更新后，同地址状态可继续分发的延迟 |

`enable_credit_visibility_delay=true` 使用可见信用反馈。
`use_explicit_idu_credit_bank=false` 使用资源查询扣除传输中预留的方式计算预算。
`global_shq_preg_gate=false` 按具体指令所需资源检查分发条件。

## 循环参数

| 参数 | 配置值 | 作用 |
| --- | ---: | --- |
| `initial_top_block_vloop_start_cycle` | 19 | 首个非空顶层 block 的 VLOOP 起点 |
| `vloop_to_dispatch_delay` | 2 | VLOOP 开始到 body 开放 |
| `idu_dispatch_start_advance` | 2 | IDU 相对 VF startup 提前开放的周期数 |
| `nested_vloop_initial_start_gap` | 1 | 初始嵌套 VLOOP 起点间隔 |
| `loop1_min_feedback_gap` | 7 | 下一层循环反馈的最小间隔 |
| `innermost_iter_dispatch_stride` | 1 | 最内层迭代开放步距 |

`isa.json.defaults` 提供 `vf_startup_cost=23` 和 `vf_drain_cost=12`。
循环控制、IDU 开放和数据依赖共同确定一条指令的实际发射周期。

## 时序查询

`ParamDB.resolve_inst()` 根据 opcode、form、dtype 和指令类别构造缓存的 Profile。
form 优先精确匹配，查询可使用 `b16→fp16`、`b32→fp32` 备选。
缺失指令参数使用 latency=9；计算指令的默认 FU/端口为 ALU/EXU01，并记录 warning。

forwarding 优先查询指令对，随后检查 producer 的 `*` 默认 consumer。
缺失 LOAD→STORE 对使用 6 cycles，其他缺失对使用 `max(0, producer_latency-3)`。
II 优先查显式指令对；缺失对在前条 latency 比后条大 1 时取 2，否则取 1。
缺失参数记录于 `model_warnings.json`，精度覆盖需结合测量证据判断。

## 理论模式

```bash
python3 main.py --trace INPUT.json --theoretical-limit-vloop-only --out_dir results/theory_vloop
python3 main.py --trace INPUT.json --theoretical-limit-vloop-only-legacy-forwarding-direct-issue --out_dir results/theory_direct
```

`apply_theoretical_limit_overrides()` 放宽窗口和队列容量，设置理论场景的传输及控制延迟。
`theoretical_limit_*` 字段覆盖对应场景参数。直接发射理论模式使用 SHQ→EXU 路径，
放宽资源约束并使用指定的 forwarding 口径。该结果用于比较结构约束下的参考周期。

## A5 模式与软件展开

`configs/cce_lowering.json` 保存目标 CANN wrapper 的物理指令及 SSA 依赖。
`isa.json.instructions.OP.memory_mode_latency` 可按 `catalog_mode` 覆盖访存 latency。
Profile 查询同时使用 opcode、form、dtype、类别和 memory mode。
`lsu_resource` 与正整数 `lsu_issue_interval` 声明共享 GSU 或块存储服务资源；
`align_state_latency` 声明状态发布间隔，默认值为 1。

forwarding 查询优先检查精确指令对，再检查明确的 opcode alias 和已有回退规则。
VDUP 表达向量源广播，VDUPS 表达标量源广播；两者使用各自的时序键。
A5 类型与实测覆盖见 [指令清单](a5_instruction_coverage.md) 和
[覆盖进度](instruction_coverage/a5/progress.md)。
