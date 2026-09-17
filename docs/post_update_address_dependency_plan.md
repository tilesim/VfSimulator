# POST_UPDATE 地址状态依赖建模开发方案

## 1. 基线与目标

本文基于本地 master 提交 `d9b19f3` 的代码审查编写。编写时工作目录实际属于 MultiSoC-membar 分支，本文不将该实验分支的 UB 地址计算能力视为 master 已有功能。实施前应确认目标分支和最新基线。

目标是补齐 load/store 的 POST_UPDATE 地址状态依赖，解释共享地址状态时不能同周期连续发射的现象，同时保留独立地址状态的并行能力。

本次不是修改 load/store latency，不是降低全局 load 吞吐，也不是引入 UB 地址重叠依赖或替换 mem_bar。Python 和 C++ 的 canonical 接口与调度行为需要同步。

## 2. 问题描述

以下两条指令共享可更新指针 p：

```cpp
vlds(r0, p, 64, POST_UPDATE);
vlds(r1, p, 64, POST_UPDATE);
```

第一条使用更新前的 p 访问内存，然后产生新的 p；第二条使用更新后的 p。即使两个 load port 和 UB 发射额度均可用，第二条也必须等待地址更新结果。

目前讨论采用的初始时序是假设更新结果在前一条指令发射后一周期可用，而非等待其完整访存 latency。该时序需要用已有 camodel 实验复核，不应宣称适用于所有指令和 SoC。

相反，两次访问若只读取 p、不更新 p，则没有此类更新依赖。两个独立指针 p、q 即使指向同一 UB allocation，也不应因为 allocation 相同而被串行化。

CCE 指针身份是地址状态的源级抽象，不必然等同于编译后的物理地址寄存器分配。预测结果与 camodel 不一致时，需要检查这一映射，而不是直接增加全局发射限制。

### 2.1 master 当前缺口

1. `api/cce_adapter.py::_memory_accesses_for_call()` 将调用 offset 合入访问偏移，没有单独区分 POST_UPDATE 的访问位置与更新步长。
2. `api/frontend/schema.py::MemoryAccess` 没有独立的地址状态身份与更新语义。
3. Python/C++ 的 Uop 和 LSU 发射检查中没有地址状态依赖。
4. load/store 依据物理寄存器压力排序，后面的 load 可能先于前面的 store 被选中；仅在成功发射时记录一个指针时间戳不能阻止尚未发射的前序更新被越过。

## 3. 建模边界与接口

### 3.1 区分三类信息

| 信息 | 含义 | 本次作用 |
| --- | --- | --- |
| UB allocation ID | 数据位于哪块内存 | 保留现有描述，不作为指针身份 |
| 地址状态 ID | 哪个可更新指针状态 | 建立地址读取与更新依赖 |
| 动态依赖记录 | 某次更新或读取何时发生 | 判断 LSU 是否可以发射 |

不要把地址状态当成普通 vector Register，不进入 vector RAT，不消耗 preg credit。

### 3.2 Canonical 描述

在 canonical 内存访问描述中显式表达以下语义，最终字段名由开发者结合现有类型确定：

- 使用的地址状态 ID，可选，缺省表示调用方未提供地址状态信息。
- 更新模式，至少区分不更新与 POST_UPDATE。
- 更新步长及明确的单位，推荐在边界归一化为字节。
- 原有 offset 表示本次访问相对当前指针的位置，不能再将 POST_UPDATE 步长当作本次访问偏移。

更新标志不能由步长是否非零推断。NORM 访问也需要描述所读取的地址状态，否则无法识别 POST_UPDATE 后接 NORM 的依赖。

CCE adapter 根据 Catalog 的参数定义识别 offset、update 和 memory operand，避免为每个 opcode 追加独立解析分支。更新步长的单位必须结合指针元素类型与指令语义处理，不对所有 store 形式盲目套用一个倍率。

指针身份应考虑作用域。独立指针变量分别持有状态；内联 cast 不应凭空创建新状态。复杂的指针赋值、重绑定、分支更新无法可靠表达时，应明确报错或报告能力不足，不能静默丢失已识别的 POST_UPDATE 语义。

旧 canonical 输入没有地址状态描述时维持原行为。显式声明 POST_UPDATE 却缺少地址状态等必要信息的输入应校验失败。

## 4. 动态依赖规则

### 4.1 建立位置

在动态指令按程序顺序进入 OoO、创建 Uop 时建立依赖。Python 使用 `RenameController.accept()`，C++ 使用 `OoOCoreMainline::accept()`。

以最终展开流的顺序为准，覆盖循环、跨迭代与 unroll；不能使用重复的静态 PC，也不能按 LSU 候选优先级建立依赖。实施时验证 accept 的顺序确实与 `stream_seq` 一致。

### 4.2 依赖语义

对每个地址状态维护最近的更新事件和当前版本尚未完成地址读取的访问：

1. 所有访问都读取当前地址版本，依赖前一个更新事件。
2. POST_UPDATE 同时产生下一地址版本，后续访问绑定到这个新版本。
3. 更新不得越过此前尚未完成地址读取的访问，避免破坏旧版本的使用。
4. 同一版本的只读访问之间不建立串行依赖。
5. 当前指令先绑定前序依赖，再登记自身读取与更新事件，避免自依赖。

地址读取暂以访存指令实际 start 为完成事件。对于“先读取、后更新”的防越过约束，第一版可要求前序读取已经 start，但不额外添加一周期。是否允许同周期读取与后续更新应通过边界测试与 camodel 校准，不能将更新结果的延迟机械套用到反依赖上。

### 4.3 事件与生命周期

建议使用独立、可共享的轻量事件记录，保存动态指令身份及 start/ready 时间。不要依赖 producer 始终存在于 LSQ 或 ROB 中。

C++ 当前调度结构存在 Uop 的多份副本，必须保证事件状态一致，不能缓存可能因容器移动而失效的裸指针。完成且无人引用的事件应可回收，避免随循环次数无限累计。

可新增一个小型地址状态跟踪器，建议职责接口如下，名称并非硬性要求：

| 建议接口 | 职责 |
| --- | --- |
| `bind(uop, access)` | 按程序顺序绑定地址依赖、创建更新事件 |
| `can_issue(uop, cycle)` | 检查更新可用时间和前序地址读取事件 |
| `notify_start(uop, cycle)` | 成功发射后发布读取完成和更新可用时间 |

不需要每周期扫描完整 ROB，也不应为所有历史访存维护两两依赖矩阵。

## 5. LSU 发射规则

在 Python `_issue_ready_lsu()` 和 C++ `issueReadyLsu()` 中，实际占用端口之前增加地址依赖检查。

```text
原有数据 ready 条件满足
且地址状态依赖满足
且 mem_bar 条件满足
且 load/store port 和 UB 额度满足
    -> 指令实际发射
    -> 发布地址读取事件
    -> 若有更新，发布更新结果可用时间
```

建议新增架构参数 `lsu_post_update_ready_latency`，初始值为 1，定义为：

```text
更新结果 ready_cycle = producer.start_cycle + lsu_post_update_ready_latency
```

第一版仅接受正整数，Python/C++ 保持同样的默认值和校验。该参数不复用指令 latency，也不放入按 opcode/form 查询的数据 forwarding 表。

地址阻塞应跳过当前候选，继续考察无关地址的候选。保留既有 load/store 压力优先级、端口数、ub_slots 和 mem_bar 行为，不在 IDU 引入阻塞。

同周期可能多次进入 LSU 发射函数，事件状态必须贯穿整个 cycle，不能在一次函数调用返回时清空。

## 6. 代码改动位置与关键函数

以下路径相对于目标仓库根目录，依据 master `d9b19f3` 核对。

### 6.1 Python 前端与接口

| 文件或函数 | 当前职责 | 本次修改 |
| --- | --- | --- |
| `api/cce_adapter.py::_memory_accesses_for_call()` | 从 CCE 调用构造内存访问 | 识别更新模式，分离访问偏移与步长，传递指针身份 |
| `api/frontend/adapter_ir.py::AdapterMemoryAccess` | adapter 中间访问描述 | 承载地址状态信息 |
| `api/frontend/schema.py::MemoryAccess` | canonical 内存语义 | 增加正式类型字段 |
| `api/frontend/value_versioning.py::ValueVersioningPass` | 将 adapter 转成版本化 canonical | 保留地址信息，不把指针塞入 vector 版本化路径 |
| `api/frontend/serialization.py` 及 canonical 校验入口 | JSON 读写与语义检查 | 字段往返、缺省兼容与非法组合校验 |
| `api/frontend/core_lowering.py::CoreLoweringPass._memory_accesses()` | 输出核心内存描述 | 传递地址字段，防止 lowering 丢失信息 |

### 6.2 Python 动态指令与调度

| 文件或函数 | 当前职责 | 本次修改 |
| --- | --- | --- |
| `core/ifu.py` | 指令展开、动态顺序与迭代信息 | 保证地址字段经过所有展开路径后仍存在 |
| `core/ooo.py::Uop` | 动态调度状态 | 增加地址依赖事件引用，不增加 preg |
| `core/ooo_mainline.py::RenameController.accept()` | 动态指令接收与重命名 | 按动态顺序绑定地址依赖 |
| `core/ooo_mainline.py::_issue_ready_lsu()` | LSU 候选选择与实际发射 | 检查依赖并发布 start 事件 |
| 架构配置读取与校验路径 | 初始化时序和资源参数 | 读取并校验更新可用延迟 |

### 6.3 C++ 对齐

| 文件或函数 | 本次修改 |
| --- | --- |
| `api/native/CanonicalVfInfo.h::CanonicalMemoryAccess` | 与 Python canonical 地址字段对齐 |
| `api/native/CanonicalVfInfo.cpp` | 增加同等语义校验 |
| `native/CanonicalVfInfoFixtureDecoder.cpp` | 支持测试与 JSON 输入字段 |
| `native/CanonicalProgramLowering.cpp::expandInstruction()` | canonical 地址描述传至动态指令 |
| `native/IFU.h::DynamicInst` | 保留地址描述，检查循环/unroll 路径不丢字段 |
| `native/OOO.h::Uop` | 共享地址依赖事件记录 |
| `native/OOO.cpp::OoOCoreMainline::accept()` | 顺序绑定依赖 |
| `native/OOO.cpp::issueReadyLsu()` | 发射检查与事件发布 |
| `native/ParamSchema.h`、`native/ParamDB.cpp`、`native/CanonicalProgramLowering.cpp` | 配置字段、读取与 canonical uarch 覆盖传递 |

此外检查 trace 输出与对外包同步流程。不要只更新工作区源码而遗漏实际使用的 wheel 或 native 构建。

## 7. 数值地址计算与本次时序修改的关系

本次必须正确区分访问偏移和更新步长，但 master 的全局 mem_bar 调度不要求完整求出每次访问的物理 UB 地址。因此，不需要为了修复发射时序把整个 UB 局部依赖实验一起合入。

若后续需要动态地址范围，按程序顺序使用“先访问、后推进”的数值递推，放在动态地址生成层。可以参考实验分支的指针状态实现，但应通过 canonical 正式字段接入，不能让正确性依赖于仅 CCE 私有的 attributes。

数值地址递推与时序事件分别维护。不能在 OoO 的实际发射顺序中直接累加同一个指针数值来计算语义地址。

## 8. 开发步骤

1. 固定 master 基线，记录修改前回归结果和已有 AABBCC 实验数据。
2. 完成 canonical 双语言字段、校验、JSON 往返和 lowering 测试。
3. 完成 CCE 解析，先验证生成的地址状态身份与更新描述，不急于比较总周期。
4. 在 Python 接收与 LSU 发射处实现事件跟踪，并添加逐条时序断言。
5. 同步 C++，用同一 canonical fixture 对比动态发射结果。
6. 跑原有回归和 camodel 对照；只有实际携带地址更新语义的输入才应产生预期时序变化。

## 9. 测试与验收

### 9.1 必须覆盖的定向测试

| 场景 | 预期 |
| --- | --- |
| 同一 p 连续 POST_UPDATE load | 后者不早于前者 start + 配置延迟 |
| p、q 独立，指向同一 allocation | 其他条件允许时可双发 |
| 同一 p 连续 NORM | 不增加地址串行约束 |
| POST_UPDATE 后接同一 p 的 NORM | NORM 等待更新结果 |
| NORM 尚未 start，后续 POST_UPDATE 已数据 ready | 后续更新不得越过前序读取 |
| 前序 store POST_UPDATE、后序 load，load 优先 | load 不能绕过尚未 start 的地址 producer |
| 地址 producer 因数据或 mem_bar 阻塞 | consumer 不能因 producer 尚无时间戳而误判 ready |
| 其他指针 ready | 不被当前指针的阻塞影响 |
| 循环跨迭代与 AABBCC unroll | 依赖遵循最终动态顺序，不因静态 PC 重复丢失 |
| producer 已离开 LSQ/ROB | 后续依赖仍可正确读取事件结果 |
| 同周期多次 LSU 调度 | 不出现第二次调用绕过延迟 |
| 缺省地址字段、JSON 往返、native 直接构造 canonical | 兼容性与语义一致 |

### 9.2 端到端对照

使用此前讨论的 load/load/add/load/mul/store 模式，循环 64 次，对比多个 AABBCC unroll 因子，分别准备 NORM 与 POST_UPDATE 版本。

记录实际 CCE、编译参数、硬件地址寄存器使用、camodel 发射日志、VfSim trace 与总周期。不能仅凭总周期接近判定模型正确，也不能通过修改 load latency 抵消地址依赖缺失。

Python/C++ 对比至少包括：动态指令顺序、地址依赖 producer、start_cycle、done_cycle 和 vf_end。涉及相同输入的向量依赖、mem_bar 与 EXQ 策略应保持一致。

### 9.3 完成标准

- 同指针依赖生效，不同指针并行不被误伤。
- 不新增 IDU 阻塞，不占用 vector preg。
- 前端到 Python/C++ 核心全链路字段不丢失。
- 新增测试覆盖实际发射时序，而不是只断言程序成功或总周期。
- 未带新语义的旧输入回归保持稳定；预期变化有原因记录。
- 未支持的指针操作与未校准的时序规则明确列出，不宣称已完整模拟物理地址寄存器。

## 10. 首版实现与验证（2026-09-17，master 工作区）

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
继续 dispatch。按本计划明确限定的“只在 LSU 检查，不在 IDU 阻塞”，
目前不能复现 U4/U8 的这部分性能下降。后续应单独设计并验证地址寄存器
scoreboard 的 IDU 约束，不应调大 load latency、更新延迟或缩小 UB 带宽来
强行匹配总周期。首版完成的是地址状态依赖机制，不是完整 SREG 微架构校准。

## 2026-09-17 分支同步说明

上文测试记录来自 master 的 6cf612c，不代表所有历史实验分支的行为相同。
本次同步仅包含 POST_UPDATE 地址发射依赖、fallback_dtype 命名和无效静态
vreg warning 清理；不增加架构寄存器预警，也不更改分发、UB 同步策略。

本分支保留旧 VFInfo 兼容入口和原有 Python UB 区间/指针状态实验，仅在其上叠加地址状态发射依赖。Native 尚无 align-state lowering，新增跨语言测试不覆盖 VSTUS/VSTAS；并未宣称补齐该旧能力。Native 对照使用 `vfsim_membar_canonical_test_runner`，不是 legacy JSON runner。Python 238 项通过，Native CTest 7/7 通过。AABBCC 八组 Python/Native 周期逐项一致。
