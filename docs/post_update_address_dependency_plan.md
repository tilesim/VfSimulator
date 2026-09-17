# POST_UPDATE 地址状态依赖建模开发方案

## 1. 基线与目标

本文最初基于 master `d9b19f3` 编写，2026-09-17 根据新执行的 camodel 积压实验修订。master 工作区已将首版 LSU 地址事件方案替换为 IDU dispatch 地址状态模型，验证见第 11 节。第 10 节保留首版历史，不作为当前开发要求。

目标是在 IDU 顺序 dispatch 时建模 POST_UPDATE 地址状态依赖，解释同指针指令不能同周期 dispatch 的现象，同时允许已进入后端的同指针 load 在资源满足时双发。

**本次修订撤销“依赖前一条 LSU start + 1”的约束，不采用 IDU 与 LSU 两层叠加地址间隔。** 实验源码和完整证据见 [POST_UPDATE 积压实验](../results/post_update_backlog_probe/README.md)，复现入口为 `tools/run_post_update_backlog_probe.py`。

本次不是修改 load/store latency，不是降低全局 load 吞吐，也不是引入 UB 地址重叠依赖或替换 mem_bar。Python 和 C++ 的 canonical 接口与调度行为需要同步。

## 2. 问题描述

以下两条指令共享可更新指针 p：

```cpp
vlds(r0, p, 64, POST_UPDATE);
vlds(r1, p, 64, POST_UPDATE);
```

两条指令分别使用更新前后的 p。需要等待的是 IDU 阶段的地址状态转发，不是等待前一条访存指令在 LSU start 或 done。

当前实验支持：第一条在 t0 成功 dispatch，第二条最早 t0+1 dispatch。两条之后如果在后端积压，可以同周期 issue。不要将 IDU dispatch、ISU issue 和 LSU_I1 混为一个“发射”事件。普通 VLDS 的积压双发已经实测；特殊 store 及其他 SoC 的具体转发时序仍需校准。

相反，两次访问若只读取 p、不更新 p，则没有此类更新依赖。两个独立指针 p、q 即使指向同一 UB allocation，也不应因为 allocation 相同而被串行化。

CCE 指针身份是地址状态的源级抽象，不必然等同于编译后的物理地址寄存器分配。预测结果与 camodel 不一致时，需要检查这一映射，而不是直接增加全局发射限制。

### 2.1 当前实现的问题与保留部分

1. 首版 CCE/canonical 已传递 `address_state_id`、`update_mode` 和 `post_update_delta_bytes`，保留这部分能力。
2. 首版在 OoO accept 时绑定事件，在 LSU start 时释放依赖，约束阶段错误。
3. 首版允许 IDU 持续分发，LSU 可绕过受阻指针选择其他指针，无法复现真实 IDU 队头阻塞。
4. 首版禁止积压的同指针 load 同周期 issue，与新实验直接冲突。必须移除，而非仅叠加 IDU 检查。

### 2.2 新实验的判别证据

在 24 条串行 VADDS、前置 store 和 VST_VLD barrier 后积压 load，得到：

| 同一 Sn[64]、#p=1 的指令 | IDU dispatch | LDQ 接收 | ISU issue | LSU_I1 |
| --- | ---: | ---: | ---: | ---: |
| 85 | 1712 | 1714 | 1799 | 1800，LDU0 |
| 86 | 1713 | 1715 | 1799 | 1800，LDU1 |

两条同周期从 LDQ issue 并进入不同 LDU，证明不能加 LSU 同指针最小 start 间隔。
同指针、独立指针、显式 offset 三个版本均通过 1024 个 fp32 输出校验。
barrier 可以提前进入后端并通过背压形成积压；不能假设所有等待都发生在 LDQ，
也不能把较早的 ISU issue 当成 UB 已执行完成。本次不顺带重构 membar。

## 3. 建模边界与接口

### 3.1 区分三类信息

| 信息 | 含义 | 本次作用 |
| --- | --- | --- |
| UB allocation ID | 数据位于哪块内存 | 保留现有描述，不作为指针身份 |
| 地址状态 ID | 哪个可更新指针状态 | 建立地址读取与更新依赖 |
| IDU 地址就绪记录 | 前次成功 dispatch 产生的更新何时可用 | 判断当前队头是否可 dispatch |

不要把地址状态当成普通 vector Register，不进入 vector RAT，不消耗 preg credit。

### 3.2 Canonical 描述

保留首版 canonical 内存访问字段，表达以下语义：

- 使用的地址状态 ID，可选，缺省表示调用方未提供地址状态信息。
- 更新模式，至少区分不更新与 POST_UPDATE。
- 更新步长及明确的单位，推荐在边界归一化为字节。
- 原有 offset 表示本次访问相对当前指针的位置，不能再将 POST_UPDATE 步长当作本次访问偏移。

更新标志不能由步长是否非零推断。NORM 访问也需要描述所读取的地址状态，否则无法识别 POST_UPDATE 后接 NORM 的依赖。

CCE adapter 根据 Catalog 的参数定义识别 offset、update 和 memory operand，避免为每个 opcode 追加独立解析分支。更新步长的单位必须结合指针元素类型与指令语义处理，不对所有 store 形式盲目套用一个倍率。

指针身份应考虑作用域。独立指针变量分别持有状态；内联 cast 不应凭空创建新状态。复杂的指针赋值、重绑定、分支更新无法可靠表达时，应明确报错或报告能力不足，不能静默丢失已识别的 POST_UPDATE 语义。

旧 canonical 输入没有地址状态描述时维持原行为。显式声明 POST_UPDATE 却缺少地址状态等必要信息的输入应校验失败。

## 4. IDU 地址状态规则

### 4.1 检查与提交位置

在 Python `core/idu.py::IDU.dispatch()` 和 C++ `native/IDU.cpp::IDU::dispatch()`
中检查。以最终动态流顺序处理窗口队头，跨 loop/unroll 保持同一地址状态的关联。
遇到地址未就绪必须停止本轮顺序 dispatch，不能 continue 跳过队头。

只有指令满足原有资源、宽度和其他 dispatch 条件并成功分发后，才登记其地址更新。
失败尝试不能占用 credit，不能更新 scoreboard，也不能对下一条制造虚假依赖。
记录的时间是 IDU dispatch，而不是经过 `idu_to_ooo_delay` 后的 OoO accept。

### 4.2 就绪表与操作规则

每个地址状态维护最近一次更新的 `ready_cycle`，可附带 producer 动态身份用于日志。

1. 当前访问检查其所有地址状态的 ready_cycle，任一未就绪则阻塞队头。
2. 成功 dispatch 的 POST_UPDATE 将对应状态设为 `cycle + 配置间隔`。
3. 不更新地址的访问不改写 ready_cycle，因此同一状态的纯 NORM 访问不额外串行。
4. POST_UPDATE 后的 NORM 仍需等待前次更新；NORM 后接更新依靠 IDU 按序取地址，不等待 NORM 的 LSU start。
5. 更新标志独立于步长，零步长 POST_UPDATE 仍按更新指令处理。
6. 同周期多次调用 dispatch 共用状态，不在函数返回时清空。

IDU 成功 dispatch 可视为本模型中的地址读取/版本捕获事件。已经进入后端的指令
不再读取一个会被后续指令修改的全局指针值，因此无需继续维护等待 LSU start 的 WAR reader 列表。
这是一种与日志一致的时序抽象，不宣称精确还原硬件物理 SREG 实现。

### 4.3 简化跟踪器

将 `core/address_state.py` 和 `native/AddressState.h` 改为 IDU 所有的轻量 scoreboard，
不再给每条 Uop 创建共享地址事件图。可采用以下职责划分，具体命名由开发者决定：

| 建议接口 | 职责 |
| --- | --- |
| `can_dispatch(accesses, cycle)` | 无副作用地判断地址状态是否就绪 |
| `notify_dispatch(inst, cycle)` | 成功 dispatch 后登记更新和日志身份 |

每个地址状态只保留必要的最新记录，不随动态指令数无限增长。每次新 VF 仿真重置状态。
不需要扫描 ROB 或 LSQ，不占 vector preg，也不引入完整的物理 SREG 分配器。

## 5. 配置与后端清理

新参数建议为 `idu_post_update_ready_latency`，默认值为 1，正整数，定义如下：

```text
地址 ready_cycle = 前一条更新指令的 IDU dispatch_cycle
                    + idu_post_update_ready_latency
```

这是地址更新转发的 dispatch 间隔，不是访存 latency，也不是 LSU II。
Python/C++ 配置文件、共享 override schema、生成文件和校验必须一致。

删除首版 `lsu_post_update_ready_latency`，遇到旧字段给出明确迁移错误，不能静默忽略，
也不能在不告知语义改变的情况下直接当成新参数使用。

移除 Python `_issue_ready_lsu()` 和 C++ `issueReadyLsu()` 中的地址 can_issue/notify_start；
同时移除 OoO accept 的地址事件绑定、Uop 共享事件引用及失去用途的生命周期代码。
不要保留一个关闭的旧 LSU 模式增加维护负担。

保留原有 vector 数据 ready、mem_bar、load/store 优先级、端口和 ub_slots 判断。
两条同指针 load 已通过 IDU 后，只要这些原有条件满足，就允许在后端双发。
不得用 SHQ 阻塞、缩小 UB 带宽或增大 load latency 替代 IDU 队头阻塞。

## 6. 代码改动位置与关键函数

以下路径相对于目标仓库根目录，包含 master 首版实现后的增量修改。

### 6.1 Python 前端与接口

| 文件或函数 | 当前职责 | 本次修改 |
| --- | --- | --- |
| `api/cce_adapter.py::_memory_accesses_for_call()` | 从 CCE 调用构造内存访问 | 识别更新模式，分离访问偏移与步长，传递指针身份 |
| `api/frontend/adapter_ir.py::AdapterMemoryAccess` | adapter 中间访问描述 | 承载地址状态信息 |
| `api/frontend/schema.py::MemoryAccess` | canonical 内存语义 | 保留首版正式字段，不另建私有属性路径 |
| `api/frontend/value_versioning.py::ValueVersioningPass` | 将 adapter 转成版本化 canonical | 保留地址信息，不把指针塞入 vector 版本化路径 |
| `api/frontend/serialization.py` 及 canonical 校验入口 | JSON 读写与语义检查 | 字段往返、缺省兼容与非法组合校验 |
| `api/frontend/core_lowering.py::CoreLoweringPass._memory_accesses()` | 输出核心内存描述 | 传递地址字段，防止 lowering 丢失信息 |

### 6.2 Python 动态指令与调度

| 文件或函数 | 当前职责 | 本次修改 |
| --- | --- | --- |
| `core/ifu.py` | 指令展开、动态顺序与迭代信息 | 保证地址字段经过所有展开路径后仍存在 |
| `core/idu.py::IDU.dispatch()` | 顺序 dispatch 与资源检查 | 队头检查地址 ready；成功分发后提交更新 |
| `core/address_state.py` | 首版 OoO 地址事件跟踪 | 简化为 IDU 地址就绪表 |
| `core/ooo.py::Uop` | 动态调度状态 | 删除地址事件引用，不改变 preg |
| `core/ooo_mainline.py::RenameController.accept()` | 动态指令接收与重命名 | 删除地址事件绑定 |
| `core/ooo_mainline.py::_issue_ready_lsu()` | LSU 候选选择与实际发射 | 删除地址 start 间隔约束，保留其他 ready 条件 |
| `core/simulator_runner.py` 及 dispatch 日志路径 | IDU 与 OoO 连接、trace | 日志区分 dispatch/accept/start，记录地址阻塞原因 |
| 架构配置读取与校验路径 | 初始化时序和资源参数 | 迁移至 IDU 参数，拒绝旧 LSU 字段 |

### 6.3 C++ 对齐

| 文件或函数 | 本次修改 |
| --- | --- |
| `api/native/CanonicalVfInfo.h::CanonicalMemoryAccess` | 与 Python canonical 地址字段对齐 |
| `api/native/CanonicalVfInfo.cpp` | 增加同等语义校验 |
| `native/CanonicalVfInfoFixtureDecoder.cpp` | 支持测试与 JSON 输入字段 |
| `native/CanonicalProgramLowering.cpp::expandInstruction()` | canonical 地址描述传至动态指令 |
| `native/IFU.h::DynamicInst` | 保留地址描述，检查循环/unroll 路径不丢字段 |
| `native/IDU.h`、`native/IDU.cpp::IDU::dispatch()` | 持有 scoreboard，队头检查与成功 dispatch 提交 |
| `native/AddressState.h` | 改为轻量 IDU 地址就绪表 |
| `native/OOO.h::Uop` | 删除共享地址事件引用 |
| `native/OOO.cpp::OoOCoreMainline::accept()` | 删除地址依赖绑定 |
| `native/OOO.cpp::issueReadyLsu()` | 删除地址检查与 start 事件发布 |
| `native/SimulatorRunner.cpp` 及 trace 路径 | 记录与 Python 同口径的 dispatch 和阻塞事件 |
| `native/ParamSchema.h`、`native/ParamDB.cpp`、`native/CanonicalProgramLowering.cpp` | 配置字段、读取与 canonical uarch 覆盖传递 |

此外检查 trace 输出与对外包同步流程。不要只更新工作区源码而遗漏实际使用的 wheel 或 native 构建。

## 7. 数值地址计算与本次时序修改的关系

本次必须正确区分访问偏移和更新步长，但 master 的全局 mem_bar 调度不要求完整求出每次访问的物理 UB 地址。因此，不需要为了修复发射时序把整个 UB 局部依赖实验一起合入。

若后续需要动态地址范围，按程序顺序使用“先访问、后推进”的数值递推，放在动态地址生成层。可以参考实验分支的指针状态实现，但应通过 canonical 正式字段接入，不能让正确性依赖于仅 CCE 私有的 attributes。

数值地址递推与时序事件分别维护。不能在 OoO 的实际发射顺序中直接累加同一个指针数值来计算语义地址。

## 8. 开发步骤

1. 保存首版基线与回归结果，把积压实验作为纠正时序位置的依据。
2. 保留前端和 canonical 测试，确认地址字段在 IDU 可直接读取。
3. 实现 Python IDU scoreboard，同步删除旧 LSU 事件约束并迁移参数。
4. 同步 C++，将首版错误的 LSU 间隔断言替换为 IDU 间隔与后端可双发断言。
5. 增加 dispatch trace 对齐，检查非零 `idu_to_ooo_delay` 下时间基准没有偏移。
6. 跑原有回归、AABBCC 对照和积压微测。先核对阶段时序，再判断总周期误差，不通过调参强行匹配。

## 9. 测试与验收

### 9.1 必须覆盖的定向测试

| 场景 | 预期 |
| --- | --- |
| 同一 p 连续 POST_UPDATE load | 后者不早于前者 IDU dispatch + 配置间隔 |
| p、q 独立，指向同一 allocation | 无队头阻塞且资源允许时可同周期 dispatch |
| 同一 p 连续 NORM | 不增加地址 dispatch 间隔 |
| POST_UPDATE 后接同一 p 的 NORM | NORM 的 dispatch 等待前次更新 ready |
| NORM 已 dispatch 尚未 start，后续 POST_UPDATE | 不因 NORM 尚未 LSU start 而阻塞 |
| 前序 store POST_UPDATE 已 dispatch、数据尚未 ready | 后续同指针访问达到地址 dispatch 间隔即可通过 IDU |
| 队头发生地址 hazard，后面是独立 load/compute | 后续指令不得绕过队头 dispatch |
| producer 因其他 IDU 条件未 dispatch | 不更新 scoreboard、不产生虚假占用 |
| 两条同指针 load 均已入队，暂时阻塞后端后释放 | 允许同周期 issue，不残留首版 LSU 间隔 |
| 循环跨迭代与 AABBCC unroll | 依赖遵循最终动态顺序，不因静态 PC 重复丢失 |
| producer 已离开 LSQ/ROB | 地址 ready 不依赖队列对象存活 |
| 同周期多次 IDU dispatch 调用 | 不绕过当周期更新限制 |
| `idu_to_ooo_delay` 非零 | 使用 IDU dispatch 时间，不使用 OoO accept/start 时间 |
| 更新间隔设为 3、旧 LSU 配置字段 | 新参数按 dispatch 生效；旧字段明确拒绝 |
| 缺省地址字段、JSON 往返、native 直接构造 canonical | 兼容性与语义一致 |

### 9.2 端到端对照

使用此前讨论的 load/load/add/load/mul/store 模式，循环 64 次，对比多个 AABBCC unroll 因子，分别准备 NORM 与 POST_UPDATE 版本。

记录实际 CCE、编译参数、硬件地址寄存器使用、camodel 发射日志、VfSim trace 与总周期。不能仅凭总周期接近判定模型正确，也不能通过修改 load latency 抵消地址依赖缺失。

同时增加本次 24 VADDS + store + barrier + 连续 load 的积压实验。真实硬件原始记录见实验 README，模型单测可直接控制后端资源以隔离地址行为，不要求先复刻整个 barrier 内部流水。

Python/C++ 对比至少包括：动态指令顺序、IDU dispatch_cycle、地址阻塞原因与 ready_cycle、start_cycle、done_cycle 和 vf_end。日志中的地址 producer 时间基准必须写明是 dispatch，不能沿用首版 start 的解释。向量依赖、mem_bar 与 EXQ 策略保持一致。

### 9.3 完成标准

- 同指针依赖生效，不同指针并行不被误伤。
- IDU 出现符合日志的地址队头阻塞；后端没有额外同指针 start 间隔，不占用 vector preg。
- 前端到 Python/C++ 核心全链路字段不丢失。
- 新增测试覆盖实际发射时序，而不是只断言程序成功或总周期。
- 未带新语义的旧输入回归保持稳定；预期变化有原因记录。
- 未支持的指针操作与未校准的时序规则明确列出，不宣称已完整模拟物理地址寄存器。

## 10. 首版实现与验证（历史记录，LSU 方案已被修订）

本节记录修订前的实现与测试结果。通过首版测试只表示两端一致，不表示 LSU 地址间隔符合硬件。涉及 bind/start 事件、旧参数和“只在 LSU 检查”的要求均已撤销，以第 1～9 节为准；新 IDU 实现与验收见第 11 节。

### 10.1 已实现

- Python/C++ `MemoryAccess` 增加可选 `address_state_id`、`update_mode`
  （`none` / `post_update`）和 affine `post_update_delta_bytes`。
  旧输入不带这些字段时维持原时序；显式更新缺少状态或增量时拒绝。
- CCE 按 Catalog 中的 memory、offset/count、update 参数识别更新。
  POST_UPDATE 的第三参数不再加入本次访问的静态 offset；增量按当前指针
  的元素宽度换算为字节。内联 cast 保留状态 ID，但使用 cast 后的元素宽度。
  独立指针声明生成独立 ID，同名内层声明不会复用外层 ID。
- 当前 master 不执行数值地址递推。`offset` 保留已有的静态 base/alias
  偏移描述，地址事件只表达初始指针之后的更新顺序；不能把这些事件记录
  直接当成已经求值的动态 UB 字节区间。
- `core/address_state.py` 与 `native/AddressState.h` 在按序 accept 时绑定
  RAW/WAR，在 LSU 发射前检查，实际 start 后发布事件。事件不依赖 ROB
  对象的存活，也不占向量寄存器。已 start 的 reader 从状态表清理，
  每个状态只保留最后一次更新及尚未 start 的 reader。
- `lsu_post_update_ready_latency=1` 已加入配置、共享字段表和 Native resolver。
  仅接受正整数；其含义是更新 producer 的 start 到地址可用的间隔，
  不是访存 latency。WAR 只要求旧 reader 已 start，不额外加 1。
- `sim_history.json` 新增 `address_state_ids` 与 `address_dependencies`。
  后者为 `[producer_inst_id, delay]` 列表，可结合 producer 的 start 检查时序。
- 未更改 IDU、向量数据 forwarding、UB 带宽或 membar 模型。

### 10.2 明确拒绝的 CCE 形式

- POST_UPDATE 搭配 VAG 地址生成器，或搭配无法解码增量的 VSSTB 复合配置。
  VSSTB 的 NO_UPDATE 仍可解析；不能再把 POST_UPDATE 静默当成不更新。
- POST_UPDATE 的目标是指针算术表达式而不是可识别的指针变量。
- 循环内声明并重复初始化的指针自身执行 POST_UPDATE。
- 复制已更新指针产生新 alias，以及在循环内快照会被更新的外层指针。
- 未知指针元素宽度、未支持的 cast、赋值及重绑定形式。

这些形式需要更完整的指针定义/快照/循环入口建模，不能通过复用原始 UB
名称绕过。普通参数指针、VF 外初始化的指针、循环外 alias 和其跨迭代更新
属于本次支持范围。VSTUS 的显式 count 按指针元素计，VSTAS 使用显式 offset；
没有可解释 increment 的 store 形式不盲目套用这一规则。

### 10.3 验证与复现

- Python 单元测试 217/217 通过（启用 Native 对齐测试，无跳过）；Native CTest 7/7 通过。
- Python 新增地址状态测试覆盖 RAW/WAR、独立指针、双发、两次 LSU 仲裁、
  producer 被数据/membar 阻塞、producer 移除、事件清理、cast、零步长、
  loop/unroll/nested loop、JSON 往返和非法元数据。
- Native 新增 `vfsim_address_state_test`，包括直接构造 canonical 输入、
  lowering、执行、非法输入与共享事件生命周期。
- 跨语言测试对同一 canonical JSON 比较动态顺序、迭代身份、依赖 producer、
  start/done 和 VF 总周期。Native 迭代日志不含 Python 的附加 induction
  描述，身份比较使用共同的 `(loop_id, iteration)`。
- Python/Native full regression 各 25 个 case 的 vf_end 与修改前严格相等，
  不是仅落在宽松容差内。没有更新回归基准。

```bash
cmake -S native -B /tmp/vfsim-post-update-build -DVFSIM_BUILD_TESTS=ON -DVFSIM_BUILD_LEGACY_MIGRATION=ON
cmake --build /tmp/vfsim-post-update-build -j2
ctest --test-dir /tmp/vfsim-post-update-build --output-on-failure
VFSIM_NATIVE_RUNNER=/tmp/vfsim-post-update-build/vfsim_native_json_runner python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/run_post_update_validation.py --out-dir /tmp/vfsim-post-update-validation --native-runner /tmp/vfsim-post-update-build/vfsim_native_json_runner
```

最后一个脚本生成自包含的 CCE、canonical JSON 和两端日志，不依赖被忽略的
results 文件。它另外运行去除地址状态字段的对照，不修改原始配置。

### 10.4 AABBCC 对照与尚未解释的硬件差距

循环 64 次，每轮 3 VLDS + VADD + VMUL + VSTS，结果单位 cycle：

| Unroll | 显式 offset（前后相同） | POST 不建地址依赖 | POST 本次实现（Python=Native） | 旧 CAModel POST 记录 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 189 | 189 | 189 | 188 |
| 2 | 194 | 194 | 190 | 187 |
| 4 | 198 | 198 | 190 | 277 |
| 8 | 189 | 189 | 187 | 298 |

CAModel 列引用 2026-09-14 的
`results/post_update_aabbcc_experiment/README.md`，本次没有重新运行 CAModel，
不将旧实验当成本次的新硬件校准结果。

U4 的原始前四条同指针 load 在 cycle 23/23/24/24 发射；增加依赖后改为
23/24/25/26。但 LSU 同时可以跳过受阻候选，从第二组独立指针取 load，
相当于将两个指针的访问交错，因此总周期不一定增加。该约束改变了启发式
发射顺序，不能根据总周期略降断言依赖没有生效。

旧 CAModel 日志记录了 IDU 的 `SREG DATA hazard`，会阻止后续指令同周期
继续 dispatch。按首版方案曾限定的“只在 LSU 检查，不在 IDU 阻塞”，
目前不能复现 U4/U8 的这部分性能下降。后续应单独设计并验证地址寄存器
scoreboard 的 IDU 约束，不应调大 load latency、更新延迟或缩小 UB 带宽来
强行匹配总周期。首版完成的是地址状态依赖机制，不是完整 SREG 微架构校准。

## 首版 LSU 实现的分支同步记录（历史）

上文测试记录来自 master 的 6cf612c，不代表所有历史实验分支的行为相同。
本次同步仅包含 POST_UPDATE 地址发射依赖、fallback_dtype 命名和无效静态
vreg warning 清理；不增加架构寄存器预警，也不更改分发、UB 同步策略。

本分支保留旧 VFInfo 兼容入口和原有 Python UB 区间/指针状态实验，仅在其上叠加地址状态发射依赖。Native 尚无 align-state lowering，新增跨语言测试不覆盖 VSTUS/VSTAS；并未宣称补齐该旧能力。Native 对照使用 `vfsim_membar_canonical_test_runner`，不是 legacy JSON runner。Python 238 项通过，Native CTest 7/7 通过。AABBCC 八组 Python/Native 周期逐项一致。
后续新执行的积压实验已确认同指针 load 可在后端同时 issue，故当前方案不再是
“保留 LSU 限制并额外研究 IDU”，而是用第 4～5 节的 IDU 规则替换首版 LSU 地址约束。

## 11. IDU 修订实现与验收（2026-09-17）

本轮以 master `5aca59e` 为代码基线，保留已有的文档修订和 camodel probe 脚本。
本节保留 master 原始验收记录。当前分支同步范围与测试结果见
[IDU 修订同步记录](post_update_idu_sync.md)。前端 canonical 字段与 CCE 解析保持不变。

### 11.1 实现

- Python/C++ scoreboard 由 IDU 独占，每个状态只保存最新更新的动态 inst ID、
  dispatch_cycle 和 ready_cycle。无共享 LSU 事件引用，也不保留 WAR reader 列表。
- 在原有资源检查通过后检查队头地址；失败 break，不扣 credit、不登记更新。
  成功指令立即登记更新，后续同周期尝试可看到它，时间基准不是 OoO accept。
- IDU 的地址读取即地址版本捕获，不等待旧 reader 或 store 的 LSU start。
  OoO accept、Uop、LSU 中首版地址绑定/检查/发布逻辑全部移除。
- 配置迁移为 `idu_post_update_ready_latency=1`，支持正 int64；旧 LSU 字段
  在配置文件及 canonical override 中均报错，不静默沿用旧配置。
- 新 `idu_address_blocked.json` 记录地址阻塞，`idu_to_ooo.json` 仍只记录成功
  dispatch，新增 `event=dispatch` 与地址依赖快照。每条依赖包含状态 ID、
  producer_inst_id、producer_dispatch_cycle、ready_cycle。
- `sim_history.json` 删除旧地址事件字段。通过 inst_id/stream_seq 与 IDU 日志
  关联 accept/start/done，避免把 dispatch 与访存实际执行混为一谈。

### 11.2 AABBCC（64 次循环）

| Unroll | 显式 offset（保持不变） | 首版 LSU POST | 新 IDU POST（Python=Native） | 已有 CAModel POST |
| --- | ---: | ---: | ---: | ---: |
| 1 | 189 | 189 | 189 | 188 |
| 2 | 194 | 190 | 186 | 187 |
| 4 | 198 | 190 | 276 | 277 |
| 8 | 189 | 187 | 290 | 298 |

U4/U8 的主要低估得到纠正。U8 仍低估 8 cycles，尚不能宣称完全校准硬件地址
寄存器分配与流水。没有调整 load latency、UB slots 或 EXQ 策略拟合这些结果。
CAModel 列沿用此前真实执行记录，本轮修改模型后未重新编译运行硬件仿真。

### 11.3 积压反例

使用同一份 24 VADDS + store + barrier + 16 load probe 源码：

| 模式 | VfSim Python/Native | 已有 CAModel | IDU 地址阻塞事件 | 后端 load 吞吐 |
| --- | ---: | ---: | ---: | --- |
| 同指针 POST_UPDATE | 159 | 157 | 13 | 连续 8 cycle，每周期 2 条 |
| 独立指针 POST_UPDATE | 159 | 156 | 0 | 连续 8 cycle，每周期 2 条 |
| 显式 offset | 159 | 156 | 0 | 连续 8 cycle，每周期 2 条 |

同指针 16 load 在 IDU cycle 26～41 逐周期 dispatch，后端在 115～122 双发。
这验证了本次最重要的两阶段区别：IDU 不允许连续更新同周期通过，但积压后
不再强制同指针 LSU start 间隔。模型没有拆分 ISU_ISSUE 与 LSU_I1，不能直接
对齐 camodel 的绝对 cycle 或解释 barrier 内部所有背压现象。

### 11.4 可复现验证

Python/Native full regression 各 25 个 case 与当前基线的 vf_end 严格相等，
Python 单测 220/220 通过（启用 Native 对照，无跳过），Native CTest 7/7 通过。
两端也逐项相等；未刷新 baseline。单测覆盖资源失败不更新、队头阻塞独立
compute、同周期重复 dispatch、零增量、跨迭代、旧 reader 尚未 start、
延迟为 3、非零 idu_to_ooo_delay=7、积压双发和非法配置。

```bash
cmake -S native -B /tmp/vfsim-post-idu-build -DVFSIM_BUILD_TESTS=ON -DVFSIM_BUILD_LEGACY_MIGRATION=ON
cmake --build /tmp/vfsim-post-idu-build -j2
ctest --test-dir /tmp/vfsim-post-idu-build --output-on-failure
VFSIM_NATIVE_RUNNER=/tmp/vfsim-post-idu-build/vfsim_native_json_runner python3 -m unittest discover -s tests
python3 tools/run_post_update_validation.py --out-dir /tmp/vfsim-post-idu-validated --native-runner /tmp/vfsim-post-idu-build/vfsim_native_json_runner
python3 tools/run_post_update_idu_backlog_validation.py --out-dir /tmp/vfsim-post-idu-backlog-parity --native-runner /tmp/vfsim-post-idu-build/vfsim_native_json_runner
python3 tools/run_cost_model_regression.py --tier full --out-dir /tmp/vfsim-post-idu-regression-python
python3 tools/run_native_cost_model_regression.py --tier full --out-dir /tmp/vfsim-post-idu-regression-native --runner /tmp/vfsim-post-idu-build/vfsim_native_json_runner
```

积压验证脚本复用 `run_post_update_backlog_probe.py` 的源码生成函数，但只运行
VfSim；硬件编译、执行与 golden 校验仍由原 probe 脚本负责。输出包括 CCE、
canonical JSON、两端日志、summary.json。项目现有 build-native 也需重建，
避免使用首版二进制；本轮已重建该目录。本仓库未发现 wheel/pybind 打包入口。
