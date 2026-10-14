# Python Core 重构记录

分支：`dev_wch/refactor`，基于用户确认的 `origin/master`（`963686e`）。
工作目录：`E:\VfSimulator_refactor`。本轮保留已经完成的 API 重构，继续整理 Python Core。

## 结果

- Core 根目录仅保留 `simulator.py` 作为公开入口；`main.py` 通过该入口运行。
- 公开输入仅为 `CanonicalVfInfo`。文件读取和 JSON 解码由 InputAPI 完成。
- 执行准备、动态展开、周期推进、调度、loop 和 membar 放入 `execution/`。
- IFU、IDU、Registers、SHQ、EXQ、EXU、LSU 放入 `uarch/`，各组件完整维护状态与局部行为；地址状态并入 IDU。
- 动态指令、Uop 和严格存储类型查询归入 `execution/`；参数放入 `params/`，结果定义和日志输出放入 `trace/`。
- 删除旧 Core 工厂、旧基类、旧 runner、旧寄存器/循环 normalization 和名称推断路径。
- 删除 ROB 容量及按序退休；完成事件由 EXU、LSU 产生，未完成登记表只用于排空。
- 保留 membar 自身的 retire、循环开放、forwarding、II、寄存器释放和信用反馈时序。
- 参数数据库默认路径适配新目录；内部 UB 对象身份不会覆盖合法的 Canonical value ID。

文件及关键接口见 [代码导览](code_structure_guide.md)。配置文件、Native C++ 和优化器没有改动。

## 验证

| 检查 | 结果 |
| --- | --- |
| Python 单元测试 | 298 项：288 通过、10 项 Native 环境测试跳过 |
| 最终 smoke 回归 | 22 cases 全部通过，与本轮 Core 改动前的周期逐项完全一致 |
| 默认 Canonical JSON CLI | `cycles_executed=35`、`vf_end_cycle=47` |
| GeLU CCE CLI | `cycles_executed=1911`、`vf_end_cycle=1923` |
| 日志对比 | 176 文件逐字节一致；22 文件移除 ROB retire/preg_old 后事件完全一致 |
| 源码语法 | 全部 Python 文件通过 Python 3.10 grammar / Python 3.11 parser 检查 |
| Scope | configs、Native、optimizer 内容与本轮开始时相同 |

新增 Core 边界测试覆盖：拒绝文件/旧字典、按声明而非名称分类、改名不改变周期和
物理调度、UB 内部 ID 不遮蔽 Register、无 ROB 排空、参数数据库默认路径。
现有 loop-carried/unroll、向量/谓词释放、POST_UPDATE、align state、共享 UB 预算和
membar 测试已迁移到新接口。删除的测试对应已删除的旧 normalization 功能。

```bash
python3 -m unittest discover -s tests -q
python3 tools/run_cost_model_regression.py --tier smoke
python3 main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu
```

## 接口与日志变化

原 `core.cost_model.CoreVfCostModel` 改为 `core.simulator.CoreVfCostModel`，不保留旧模块
转发。原 `run_payload()` 已删除；Canonical JSON 先通过 `InputAPI.load_json()` 读取。
Core 依赖由组件构造参数显式传入，移除旧 Core 实例的大范围字段转发。

`sim_history.json` 不再包含指令 `retire` 事件和 `preg_old` 字段。
`membar_history.json` 中的屏障 retire 保留。
`model_warnings.json` 移除旧空 `vreg_capacity_warnings`，仍输出指令参数 fallback。

当前执行能力限制保持不变：不自动推导 UB 地址区间依赖；显式 dependency 和不支持的
unroll 形式在准备阶段拒绝。Native 跨语言一致性尚未在本轮环境验证，后续 C++ 重构时处理。

## 后续调整：移除三端口实验开关

删除 Python 的 `--three-ports`、`CoreVfCostModel.three_ports` 和三端口端口选择分支。
`three_ports_mode` 从 override 支持列表移入废弃字段列表，并同步生成的共享配置合同。
当前 Core 拒绝 `issue_ports > 2`；通用队列/执行组件仍按端口数量组织状态，供后续 A6 复用。
三端口能力将由独立的 A6 架构实现。其他配置整理和 C++ 执行核心重构仍留待后续。

此调整验证：303 项 Python 测试（293 通过、10 项 Native 测试跳过）；默认程序仍为
47 cycles，GeLU CCE 仍为 1923 cycles。新增测试覆盖旧 CLI/构造开关拒绝、JSON/Builder
废弃字段拒绝、直接 Canonical/配置文件不能启用三端口，以及默认双端口调度。

## 后续调整：删除参数缓存统计

移除模型的公开统计开关、结果中的参数缓存统计项，以及 ParamDB 的命中/未命中计数
和统计查询方法。Profile、forwarding、II 和指令参数缓存仍保留。
缓存测试改为直接验证 Profile 对象复用，并验证缓存命中时不会重新查询或计算参数。

此调整验证：303 项 Python 测试（293 通过、10 项 Native 测试跳过）；默认程序仍为
47 cycles，返回结果不再包含缓存统计。此前三端口移除改动一并保留。

## 后续调整：通用校验归入 API

新增 `InputAPI.from_canonical()`，接收直接实例化的 Canonical 对象。CCE、JSON、Builder
和直接对象统一使用 `api/common/validator.py::require_valid_canonical_vf_info()` 输出：
检查对象类型、执行一次通用语义校验、返回原 Canonical 对象，不引入包装类型或校验标记。
Core 删除通用 validator 的导入和调用，保留类型检查、当前执行能力检查和运行期资源约束。

Builder 的 `build()` 原本已有校验；此前缺失的是直接实例化对象的接收入口。修改 API
输出对象中的 Mapping 后，调用方需重新通过 API 接收。Core 的正式调用前提是输入已经
经过 API 校验，不再在每次预测时重新完整校验。

此调整验证：306 项 Python 测试（296 通过、10 项 Native 测试跳过）。测试通过记录实际
validator 函数执行次数，验证 CCE/JSON/直接对象/Builder 从输入接收到 Core 预测都只调用
一次完整语义校验，并验证合法但当前不可执行的 unroll 仍由 Core 能力检查拒绝。

GeLU CCE 复核仍为 `cycles_executed=1911`、`vf_end_cycle=1923`。

## 后续调整：参数模块按职责重构

`params/` 收敛为 `loader.py`、`types.py`、`database.py`、`uarch.py`、`fallback.py`。
loader 统一路径选择和 JSON 读取；types 定义 Profile、UarchParameters、VfTiming 和加载数据；
ParamDB 保留参数查询及缓存；uarch 复用已解析基础配置、合并 override 并处理架构约束和
理论场景；fallback 保留现有 form 备选和缺失参数规则以及 warning 输出。

删除 TimingQuery、profile.py 和 compat.py，不保留旧模块转发。执行组件直接读取结构化
微架构字段与 Profile 的 latency、FU、allowed_exu_ports，指令对参数直接查询 ParamDB。
端口标记在创建缓存 Profile 时解析。Profile 由同一 ParamDB 创建并拥有，规范指令类别
也进入缓存键，避免运行时复制 Profile 后破坏归属检查。EXU 历史中的重复 opcode/form
字段随之删除，使用缓存 Profile 保存的字段。

默认值集中在 UarchParameters，文件中的已声明配置值不变。可选参数文件缺失仍按现有
规则回退；格式错误、无效参数单元格和错误类型会明确报错，不再吞异常当作缺失参数。
旧配置格式和已有回退规则暂保留，具体参数值及格式迁移留到后续 config 重构。

验证：311 项 Python 测试（301 通过、10 项 Native 测试跳过）；22 个 smoke case 周期
逐项完全一致，198 个日志文件逐字节一致。默认 fixture 仍为 47 cycles，GeLU CCE 仍为
1923 cycles；同一 fixture 的理论模式和直接发射理论模式均与改动前一致（45 cycles）。
新测试覆盖统一路径优先级、可选文件格式错误、无效 forwarding/II 单元格、结构化配置
合并与不可重绑定，以及合法端口解析和 Profile 缓存归属。configs、API、Native 和优化器
内容保持不变。

## 后续调整：对齐状态归入 Registers

`AlignProducerRecord`、`AlignGeneration` 从 `common/instruction.py` 移入
`uarch/registers.py`。对齐状态初始化、分组及生产者/消费者绑定也从 LSU 移入 Registers；
开始和完成时间通过现有 `on_uop_start/on_uop_done` 通知更新。Registers 提供
`align_ready_cycle()`，LSU 综合对齐就绪周期、普通寄存器依赖及访存资源进行仲裁。
Uop 保留记录引用，类型导入使用 TYPE_CHECKING 避免运行时循环依赖。

验证：311 项 Python 测试（301 通过、10 项 Native 测试跳过）；22 个 smoke case 的
周期完全一致，198 个日志文件逐字节一致。另对比 5 个对齐状态案例，覆盖加载初始化及
重新初始化、多个存储生产者、空状态组、谓词存储独立状态以及消费时封闭状态组；周期
完全一致，47 个日志文件逐字节一致。Python 3.10 语法检查通过。

## 后续调整：结果定义合并到输出模块

将小型 `SimulationResult` 定义合并到 `trace/writer.py`，删除 `common/result.py`，
Engine 从 writer 导入结果类型。周期计算仍在 Engine 中，结果字段与 `to_dict()` 行为
保持不变，关闭日志输出时仍返回完整的周期结果。代码导览和 README 同步更新。

验证：311 项 Python 测试（301 通过、10 项 Native 测试跳过）；默认 CLI 仍为
`cycles_executed=35`、`vf_end_cycle=47`，日志及 Perfetto 文件正常输出。Python 3.10
语法检查通过。

## 后续调整：取消 Core common 目录

将 `common/instruction.py` 移至 `execution/instruction.py`，保留独立的动态指令与 Uop
数据模型；将 `ValueStorageLookup` 合并至已有的 `execution/prepare.py`。删除
`core/common/`，直接更新所有组件、日志和测试引用，不保留兼容转发模块。之前已完成的
对齐状态归入 Registers、结果定义归入 trace/writer 调整一并保留。

验证：311 项 Python 测试（301 通过、10 项 Native 测试跳过）；默认 CLI 仍为
`cycles_executed=35`、`vf_end_cycle=47`，9 个日志文件与目录整理前逐字节一致。
Python 3.10 语法检查通过，代码及测试无旧 `core.common` 导入。

## 后续调整：IDU dispatch 归入 Scheduler

将 IDU.dispatch 移至 Scheduler.dispatch_idu，统一前端 dispatch 和计算后端调度的
代码归属。IDU 保留窗口、宽度、地址更新状态及基本接收/移出操作，不保留 dispatch
转发入口。循环开放、资源预算、地址依赖判断及 dispatch/阻塞日志由 Scheduler 处理；
DispatchBudget 也移入 scheduler.py。Engine 保留预算计算、资源预留、传输延迟和 admit
重命名/入队流程，只更新构造顺序和调用位置。日志 writer 和现有测试同步迁移。

保持选中指令时发布地址更新、批量选定后通知循环控制的时机，以及每周期各阶段顺序。

验证：311 项 Python 测试（301 通过、10 项 Native 测试跳过）；22 个 smoke case
的周期完全一致，198 个日志文件逐字节一致。另对比 10 种配置，覆盖 0/1/7 周期传输、
显式/非显式信用、受限寄存器、直接后端发射和两种理论模式，周期及 90 个日志文件均
完全一致。Python 3.10 语法检查通过。

## 后续调整：按组件状态与局部行为组织

保留 IFU、IDU、SHQ、EXQ、EXU、Registers 和 LSU 的独立文件，每个组件共同维护
状态、局部约束与状态转换。IDU dispatch 及日志归回 IDU，DispatchBudget 和
AddressStateTracker 合并至 idu.py，删除单独的 address_state.py。

SHQ 负责等待指令的就绪状态及信用释放；Registers 提供 ready_cycle 查询；EXQ
负责实际/准入占用、容量检查、接收/排队预测及各 FU 队首选择；EXU 负责端口可用、
在执行容量、II、执行开始和完成。Scheduler 保留 SHQ→EXQ 的跨端口策略和计算流转，
不再遍历 EXQ 内部队列、读取 EXU 发射历史或包含 IDU 的局部规则。删除未使用的
总在执行计数及预测调用中的冗余 opcode/form 入参。Engine 保持唯一周期、阶段顺序、
资源预算、传输延迟和重命名入队流程，不引入状态机框架或事件总线。

验证：316 项 Python 测试（306 通过、10 项 Native 测试跳过）；新增 5 项组件生命周期
测试覆盖 FU 队首阻塞、接收延迟、完成后恢复容量、准入计入在执行指令、不同 II 规则
和延迟信用仅发布一次。22 个 smoke case 周期一致，198 个日志文件逐字节一致；
另对比 11 种计算调度配置（110 个日志）和 10 种前端/理论配置（90 个日志），周期与
日志均一致。Python 3.10 语法检查通过。API、configs、Native 与优化器均未改动。
