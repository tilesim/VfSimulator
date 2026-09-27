# 物理谓词寄存器建模方案

日期：2026-09-22；2026-09-23 确定释放规则与当前 vector 模型一致，并收紧首版语义边界。
开发基线：`vfinfo-core-api-unification`，提交 `b534075`。
状态：首版谓词端到端建模已接通（Python/Native）；未测全时序及 PST/PLD 回放仍不作为已完成项。
适用范围先限定为当前 A5 Python/Native 主线。

## 实施记录（2026-09-23）

### 当前完成情况

- Canonical v2 增加 PredicateRegister/bool，Catalog 驱动 PSET、比较、VSEL 和 masked
  指令的输入输出；JSON、builder、CCE、Native validator/decoder 同步。
- PSET 是普通 compute 指令，暂按 ALU/EXU01，占用 SHQ/EXQ 和发射带宽。
  inline PSET 也生成独立定义并保留源码位置，不再静默忽略。
- Python/Native 的 IDU 分别预检 vector/predicate credit，原子扣账；OoO 使用独立
  freelist/合法 ID 集合，共享按物理 ID 标识的 generation、RAW 和 start+4 生命周期算法。
  谓词不占向量寄存器。释放要等待全部消费者引用归零、producer pending 解除，
  不额外等待后续同名寄存器覆写。
- 循环写集合和 alias 统一识别两类可重命名 storage。初始化过的谓词沿用通用
  entry/back-edge/exit；循环内首次生成的值通过 entry=null 的仅出口关系导出，不伪造 live-in。
- 新日志 `idu_resource_blocked.json` 区分两类 credit；history 展示谓词物理 ID 和池余量。
- 语义未覆盖的谓词指令、live-in、MODE_MERGING 明确拒绝。PST/PLD 未开放，
  不能将编译器发生谓词 spill 后的时间直接与无 spill 源码预测比较。

兼容策略：含谓词输入使用 schema_version=2；v1 仅保留原有无谓词合同，禁止使用
PredicateRegister。两者共用 Canonical lowering/Core，不增加另一套执行路径。
共享 JSON Schema 文件暂保留历史文件名 `canonical_vf_info_v1.schema.json`，
版本字段接受 1/2，validator 执行对应版本语义。旧输入不能无损恢复丢失的 mask；
不得自动升级成 v2 或伪造全有效谓词。

验证命令（构建目录可自行选择）：

```bash
cmake -S native -B /tmp/vfsim-predicate-foundation-native -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/vfsim-predicate-foundation-native -j4
ctest --test-dir /tmp/vfsim-predicate-foundation-native --output-on-failure
VFSIM_NATIVE_RUNNER=/tmp/vfsim-predicate-foundation-native/vfsim_native_json_runner python3 -m unittest discover -s tests
python3 tools/run_cost_model_regression.py --tier smoke --out-dir /tmp/vfsim-predicate-complete-regression
```

Python 262 项：261 通过、1 跳过；Native 8/8 通过。新增测试覆盖两池隔离、相同释放合同、未定义/未知谓词拒绝、
零次/嵌套/alias/unroll、容量压力和 Python/Native 逐条 start/done 对齐。
完整回归 Python/Native 各 25 个 case 通过现有守卫，逐项周期一致；Python 的 25 项
也与本次开发前结果逐项一致。没有修改 baseline，历史三项差异仍见下表。
完整结果位于 `/tmp/vfsim-predicate-complete-full`、`/tmp/vfsim-predicate-complete-native`；
复现时将上述回归命令改为 `--tier full`，Native 使用
`tools/run_native_cost_model_regression.py --runner <native runner> --tier full --out-dir <output>`。

已有 CAModel golden 通过的 `predicate_select_fp32.cce`，本次两侧源码预测均为 60 cycles。
已有 8 轮压力 CCE 的源码预测如下（单位 cycles，不是 CAModel 精度校准）：

| 每轮比较数 N | Python | Native |
|---:|---:|---:|
| 4 | 179 | 179 |
| 5 | 208 | 208 |
| 6 | 232 | 232 |
| 7 | 256 | 256 |
| 8 | 286 | 286 |
| 16 | 498 | 498 |

N=32 的源码中，32 个比较结果和仍被使用的全有效 mask 同时存活，超过 32 个物理
谓词槽位；模型不会偷偷扩容或自动插入 spill。需要编译后 PST/PLD 回放能力才能
对该路径做完整硬件时序比较。完整 PSET timing 测量仍按第 7 节的调查清单推进。

### 第一阶段基础改造记录（历史）

用户确认本阶段按 PSET、VCMP、VSEL 均支持 EXU0/EXU1、占用 SHQ 槽位开发。
EXU01 是当前建模假设，不是已经测得的完整硬件资格集合；缺失时序继续 warning/fallback，
不将其预测结果用于精度校准。该确认解除完整 PSET 时序调查对功能开发的前置阻塞。

- 已将 Python vector 的 RAT、freelist、producer、generation、引用计数和回收条件
  收敛到 `core/physical_register_bank.py`，现有 Core 使用该资源池，暂保留旧字段引用。
- 已删除 Python 按 opcode 选择 consumer release offset 的内部路径；共享配置契约
  将该旧字段列为废弃项，统一使用全局 `consumer_release_start_offset`。
- 新增 `tests/test_physical_register_bank.py`，验证两个资源池隔离及现有 vector
  last-use +4、多个乱序消费者、重复源、producer pending 和 stale generation 行为。
- 尚待实现：Canonical 契约升级、PSET/VCMP/VSEL Catalog 与 CCE 接入、统一可重命名
  storage 分析、谓词 IDU credit/rename/ready/logging，以及 Native 对应实现。
  资源池测试中的 predicate 实例不代表正式预测入口已经支持谓词。

本阶段验证：

- Python：`python3 -m unittest discover -s tests`，246 项，239 通过、7 跳过。
- Native：独立 `/tmp/vfsim-predicate-foundation-native` Release 构建，CTest 7/7 通过。
  此轮 Native 只同步废弃配置校验和测试，尚未接入谓词资源池。
- 完整回归 25 个 case 通过现有守卫；结果在
  `/tmp/vfsim-predicate-foundation-regression/compare_summary.json`。
- 旧基线的三项差异不是本阶段造成的。将 HEAD 的 `core/ooo.py` 和
  `core/ooo_mainline.py` 在独立 Python 进程内加载后，使用相同 canonical 输入复跑：

| Case | 修改前 HEAD | 本阶段 | 差异 |
|---|---:|---:|---:|
| online_update_i64_u1 | 434 | 434 | 0 |
| gelu_poly_i96_u6 | 1746 | 1746 | 0 |
| gelu_poly_i96_u8 | 1717 | 1717 | 0 |

未修改回归 baseline；上述验证不是谓词端到端或 CAModel 时序精度验收。

## 1. 目标与边界

### 2026-09-24 循环出口与版本定义修复

- 删除按 PredicateRegister 过滤循环 written 集合的分支。统一分析两类资源的首次读写：
  入口已有定义或 read-before-write 时建立入口关系；无入口的 write-first 值使用
  `LoopCarriedValue.entry_value_id=null`，表示没有回边入口，只有最后一轮的出口。
- Python/Native validator、JSON decoder、lowering 和 IFU 同步支持此 v2 表达。
  没有入口值的循环必须可证明 count>0。count=0 或未解析的符号次数不能导出首次定义；
  读取这种未定义出口报错。零次循环中未向外读取的局部值不建立出口，仍允许执行。
- predicate read-before-write 仍由无 live-in 规则拒绝；不通过生成假的 PSET 修补。
- 旧迁移 adapter 显式声明历史 vector slot 的 live-in 合同，通用版本化不按输入来源或
  predicate opcode 选择循环算法。旧无谓词 fixture 的预测保持不变。
- CURRENT=2、SUPPORTED={1,2} 与 LEGACY_EMISSION=1 独立定义；非法版本诊断提供
  `supported_versions=[1,2]`。builder/adapter 根据是否包含谓词或仅出口值选择输出版本。
- 测试包含 vector/predicate、1/4 次循环、unroll=1/2、嵌套循环、循环后使用、
  零次循环、符号次数的正值/零值/未解析，以及 Python/Native 开始周期对齐。
  Python 265 项：264 通过、1 跳过；Native 8/8 通过；25 项完整回归周期无变化。

将谓词作为真正的数据流和独立物理资源，贯穿 CCE、Canonical、动态展开、
IDU、重命名、调度和资源释放，而不是仅检查 mask 参数是否合法。

- 建模 32 个物理谓词寄存器，与现有 68 个物理向量寄存器独立计数。
- 不限制架构谓词寄存器数量，不在 VfSim 中自动插入编译器 spill。
- 保留逻辑谓词身份和各次定义；不建模架构容量不等于删除逻辑身份。
- 支持谓词 RAW 依赖，重命名消除 WAR/WAW；支持循环、跨循环及嵌套循环。
- Python/C++ 使用相同 Canonical 合同、资源语义和测试输入。
- 不改变现有 EXU 拓扑、分发策略、vector 释放规则或 membar 同步规则。
- 谓词沿用当前 vector 的动态 last-use + consumer start 后 4 cycle 释放机制，
  仅独立管理物理资源，不额外要求同名逻辑寄存器被覆写。
- 不模拟每个 mask bit 的数值，不因 mask 为全零而擅自省略指令。
- A6 参数不得直接套用此次 A5 观测，后续按 SoC 单独校准。
- 首版拒绝 predicate live-in、MODE_MERGING 及 Catalog 语义不完整的谓词指令。
  时序参数缺失可 fallback；谓词操作数语义缺失不可 fallback。

## 2. 日志证据及置信范围

实验和复现脚本：`cce_code/predicate_select_test/`。
详细结果：`cce_code/predicate_select_test/PRESSURE_RESULTS.md`。
原始日志：`results/predicate_pressure_a5_20260922/n*/`，可能被 Git 忽略。
这些是 CAModel 观测，不是硅片实测。

### 2.1 独立资源和重命名

- VF 初始 IDU 状态：`ooo=(preg:32, vreg:68)`。
- PSET/VCMP 写谓词时扣减 preg，不扣减 vector 目的寄存器 credit。
- ISU 有独立 `SRC_PREGS`/`DST_PREGS`，同时记录 `v_idx` 和 `p_idx`。
- N=4 时，同一架构 P2 在前三轮映射到物理编号 1、5、9。
- 所有案例观察到的物理谓词编号为 0..31。
- N=32 时 cycle 1732 出现 `OOO no avail phy preg`，同时 `preg:0 vreg:64`。

### 2.2 释放时间

对 7 个案例按动态 instr_id 关联 EXU/LSU 发射和 ISU 释放事件：

| 最后读取事件 | 样本数 | ISU 内部释放 | OOO 返回 IDU credit |
|---|---:|---|---|
| VSEL 的 EXU start | 524 | start + 3 | 内部释放 + 1 |
| PSTI 的 STU_IB_BUF.RECV | 334 | recv + 3 | 内部释放 + 1 |

纳入 `OVERWRITE_PREDST` 后，全部案例中每周期内部释放数量与下一周期
`send_stat_to_idu` 的 preg freed 数一致。未发现这些案例中的释放队列积压。

示例 N=4、VSEL id=71：1709 开始执行，1712 内部释放，1713 返回 credit。
PSTI 的起点是读取源的 buffer 接收事件，不是最终写入 UB 的时刻。

不能据此声称所有消费者、所有条件下均无条件 start+4 释放：

- N=16/32 还有 28/43 个 `OVERWRITE_PREDST` 释放事件。
- N=4 的前 7 轮共释放 28 个比较结果，最后一轮的 4 个结果未在 VF 结束前
  逐项返还；全有效 mask 也没有按普通 last-use 直接返还。
- 这些记录证明存在覆盖触发的回收路径，但不能证明所有值必须等待覆写。
  本次不据此增加覆写门槛，源码级模型统一采用第 7 节确定的 last-use 近似。
- 日志中的 reg_free_buf_size=15、reg_cycle_free_num=5 是释放队列相关字段，
  不是物理谓词池容量。本阶段不凭字段名称增加未验证的队列限制。

## 3. 当前缺口

| 层次 | 当前情况 | 需要改变 |
|---|---|---|
| Canonical | 有 OperandRole.PREDICATE，无独立谓词 storage | 增加 PredicateRegister 类别 |
| Catalog | 多个 predicate 参数 direction=ignore | 变成真实输入，定义谓词输出签名 |
| CCE adapter | 合法 predicate 参数返回 None，pset 调用被跳过 | 保留 mask 值和 producer |
| ValueVersioning/IFU | 主要围绕普通 Register 做版本和 last-use | 扩展到两种可重命名资源 |
| IDU/OoO | 一套物理 vector credit 和 RAT | 按资源类别独立管理 |
| Native/JSON | 无相应类别和运行期字段 | 同步 schema、decoder、validator 和 Core |

不能只给 `vector_bool` 改一个 dtype；否则可能进入 vector freelist，产生错误压力。
当前代码变量 `preg` 常表示 physical vector register，而 camodel 的 `preg`
表示 predicate register。新增接口必须避免复用这个歧义。

## 4. Canonical 输入合同

### 4.1 值、角色与资源类别

建议新增 `StorageKind.PREDICATE_REGISTER = "PredicateRegister"`；现有
`Register` 继续表示 vector，不大范围改名。

`CanonicalValue` 沿用 definition_id、logical_id、producer_node_id，谓词
dtype 使用 `bool`。B8/B16/B32 等掩码解释方式由指令 form/config 明确表达，
不通过名称或把 bool 当作 FP32 来推导资源类型。

- governing mask 输入：role=predicate，storage=PredicateRegister。
- 谓词逻辑运算的数据输入：role=source，storage=PredicateRegister。
- 比较/PSET 等谓词输出：role=destination，storage=PredicateRegister。
- value 的 storage 决定资源类别；role 表示该指令中的用途，二者不能混用。
- 不允许 mask 参数引用普通 vector 或 Scalar 值来绕开谓词 credit。
- 所有 PredicateRegister value 必须有 producer_node_id；为 None 时 validator
  明确拒绝，不创建仿真前 live-in 分配流程。producer 必须是合法定义来源；
  loop exit 仍可由对应 loop 节点产生，但其初始 entry 必须追溯到程序内定义。

示例：

```text
PSET.B32                           -> all.def0 : PredicateRegister
VCMP_GT.fp32(a.def0, b.def0,
            predicate=all.def0)    -> mask.def0 : PredicateRegister
VSEL.b32(x.def0, y.def0,
         predicate=mask.def0)      -> result.def0 : Register
```

数据依赖仍从 input definition 的 producer 推导，不新增第二套手写 DATA 边。

### 4.2 Catalog 与 CCE

Catalog 声明普通源、mask 源、谓词目的和配置参数，统一 binder 负责绑定。
不得按变量前缀识别 mask，也不得在 OoO 热路径按 opcode 猜寄存器类别。

首先覆盖本实验及常见链路：PSET、VCMP/VCMPS_GT、VSEL、带 mask 的
VADD/VADDS/VDUP/VSTS。比较指令不能沿用“普通 vector destination”的 binary 签名。
其他比较条件和谓词逻辑运算按签名族登记，不为单个案例编写解析特判。

- 带初始化的 `vector_bool p = pset_b32(PAT_ALL)` 必须生成真实 PSET 定义。
- 独立 PSET 赋值和 mask 重定义按源码顺序生成新版本。
- inline `pset_*()` 按求值位置展开成明确 producer；首版不做隐式 CSE 或循环外提。
- mask alias 是零成本 value binding，不生成虚假的执行指令；参与循环状态分析。
- 对带执行模式的调用，首版只支持 MODE_ZEROING；MODE_MERGING 明确报不支持，
  不能静默降为 zeroing。未来通过显式 `dst.old + inputs + predicate -> dst.new`
  扩展 Catalog 的 read-write operand、版本化和 lowering 后再支持。
  VSEL 等无此模式参数的合法签名不要求人为补一个 MODE_ZEROING。
- P0 的特殊语义未确认，不自动把某个全有效 mask 当作不占资源的硬件常量。

### 4.3 校验、序列化与兼容

同步 Python schema/validator、共享 JSON Schema、Native 类型/validator、
decoder、builder、Catalog 生成表和 fixture。检查谓词的 producer/output
双向一致性、dtype、作用域、循环回边、alias 和未定义读取。
对程序化构造和 JSON 输入执行同样的 live-in、模式和谓词签名检查，不能只在
CCE parser 拒绝而允许其他入口绕过。诊断保留节点路径和 source location。

本次会增加 enum 并将过去忽略的 mask 变为必需语义，应升级 Canonical 契约版本，
不能让新旧 consumer 在相同版本下静默得到不同依赖。首版 v2 表达谓词，v1
按旧无谓词合同兼容，均经同一 Core lowering；旧 program JSON 的迁移保留为独立工具。

旧输入缺 mask 时无法恢复原始谓词计算，迁移工具必须报告信息缺失；不得静默
生成全有效 mask。确需全有效假设时由调用方显式选择，并在迁移产物中记录。

## 5. 动态展开与物理重命名

### 5.1 IFU

统一定义 `renameable storage = Register | PredicateRegister`，通过共享的
storage 分类 helper 判断，而非到处添加 opcode/predicate 特判。
统一将 Vector 和 Predicate 视为可重命名值，在同一套动态 value instance、
loop entry/back-edge/exit 和 last-use 分析中处理，同时保留资源类别。
覆盖直线、unroll=1、unroll>1、串行/嵌套循环及零迭代，不能只在 unroll 分支补逻辑。

Python 和 Native 都应审计目前只判断 Register 的路径：循环写集合分析、
loop-carried 构造、alias、Core lowering、IFU 动态版本、value instance、
last-use、IDU 资源预检/原子扣账以及 OoO RAT/freelist/generation/释放事件。
逻辑数据流阶段共享同一算法，到物理分配阶段才按 storage 选择 vector 或
predicate bank。不得为 PSET、VCMP 或某个测试单独实现循环规则。

### 5.2 资源管理

通过资源类别参数化已有分配/引用计数/代际号管理，避免复制整套 OoO。

```text
RegisterBank(Vector):    capacity=68, RAT, freelist, lifecycle, credits
RegisterBank(Predicate): capacity=32, RAT, freelist, lifecycle, credits
PhysicalRef = (bank, index, generation)
```

上述是内部逻辑结构，不强制要求特定类名。旧 vector 默认行为必须保持不变。

- Uop 保留分类型 source/destination 绑定，不用字符串编号判断类型。
- 源读取绑定当前动态版本；写目的分配新物理版本，更新对应 RAT。
- 同一物理槽被复用时递增 generation，防止迟到的释放事件误释放新值。
- 首版不支持 live-in 谓词，必须在 validator 阶段拒绝；资源层不隐式补分配。
- alias/重复源引用必须去重或严格配对引用计数，不能多减、漏减。
- 一条指令需要多类目的资源时，IDU 预检全部需求，再原子分配；失败时不部分扣账。
- 谓词池耗尽只阻止需要新谓词目的的指令通过资源检查；是否阻塞后续指令仍遵循
  现有 IDU 顺序规则，不借本次开发增加新的 IDU 乱序能力。

## 6. Ready 与时序参数

### 6.1 Timing fallback 与语义 fallback

只有完整的操作数和资源语义已确定，才允许对缺失 latency、forwarding、II
使用 ParamDB 默认值并输出 warning。

任何涉及谓词输入或输出的指令，都必须有完整的 Catalog 声明：参数方向、
role、storage 约束、form 和合法模式。缺失或不完整时在 binder/validator
明确报错，禁止进入 generic compute fallback。包括以未知 opcode 配合
PredicateRegister 值构造的 Canonical 输入，即使调用方填写 compute class
也不能绕过首版限制。不能把谓词输出当 vector、忽略 mask 输入、丢失 RAW
依赖或使用错误的物理池。

未登记的 CCE 调用无法证明其语义时，也不能靠忽略无法识别的参数来继续预测。
首版范围外的谓词指令 fail closed；这不等于取消无谓词指令既有的时序 fallback。

### 6.2 Ready 与执行资源

计算/STORE ready 必须同时满足所有 vector、predicate 和其他既有依赖。
不能只检查普通 vector 源；没有 vector producer 的谓词 store 也不能永远卡住。

- PSET -> VCMP/VADD/VSTS、VCMP -> VSEL 等使用 ParamDB 的 producer/consumer 参数。
- 物理资源类别不决定 EXU 归属；EXU 资格和 FU 类型仍由 ISA 配置决定。
- 缺失 latency/forwarding/II 使用现有明确 fallback 并 warning，不伪造实测值。
- PSET 必须生成真实指令和谓词 definition，不能当作零成本前端初始化丢弃。
  本阶段 EXU 资格按用户确认暂设 EXU01，并占用 SHQ/EXQ 和 compute 发射资源；
  FU 类型及未测时序仍需继续调查，不将默认 ALU fallback 误记为实测结论。
- PST/PLD 作为后续 spill 回放支持项：独立 Catalog 签名、STORE/LOAD 类别、谓词
  输入或输出，保留 LSU/UB slot/membar 规则。首次支持不能错误复用 vector 目的类型。
- 不因数值 mask 的真假改变 LSU 数据搬运范围或发射数量；本次不是数据值仿真。

### 6.3 正式实现前的 PSET 调研

当前简单案例已经确认 RV_PSET.B32 经过 SHQ -> EXQ1 -> EXU1：SHQ 接收 1646、
EXQ1 接收 1647、EXU1 开始 1648、退休 1653。VCMP_GT 同样经过 SHQ/EXQ1，
VSEL 经过 SHQ/EXQ0；见 `results/predicate_select_fp32/camodel_20260922/` 的
`core0.veccore0.rvec.ISU.dump` 与 `core0.veccore0.rvec.EXU.dump`。
这些记录不能证明完整 EXU 资格、FU 类型或所有 form 时序已校准。
按用户确认的资源假设先开发功能，以下调查继续作为时序校准任务逐项记录：

| 调查项 | 测试与判据 |
|---|---|
| pset_b8/b16/b32 的映射 | 分别编译运行，按 PC 对齐实际 RV opcode、form |
| 执行单元类型 | 对照 IDU/SHQ/EXQ/EXU 日志，确认 ALU/SFU/控制或其他路径 |
| 合法 EXU 集合 | 构造独立 PSET 和竞争场景；单次只出现某端口不能证明 only |
| latency | 按动态 instr_id 对齐 start/retire，分别记录三种 form |
| self-II | 连续独立 PSET，同 EXU 相邻发射；排除队列和依赖造成的间隔 |
| PSET -> VCMP forwarding | 数据源提前 ready，隔离谓词依赖并检查实际消费者 |
| PSET -> VSEL forwarding | 两个候选 vector 提前 ready，避免其他源成为瓶颈 |
| PSET -> 普通 masked compute | 测 VADD 等，确保 vector 源已 ready |
| PSET -> masked store | vector 数据提前 ready，区分源读取/buffer 接收与 UB 发射 |
| 三种 form 是否一致 | 各项分别实测；不按 b32 结果复制 b8/b16 参数 |
| SHQ/EXQ 和分发占用 | 检查入队、出队、资源扣减及同周期普通 compute 竞争 |

防止重复常量 PSET 被编译器合并或外提；以实际 PC/RV 流而非源码调用次数
统计。维持相同输入、明确控制变量，不能把竞争 stall 当成 intrinsic latency、
forwarding 或 II。观察到的单个周期差未经隔离验证只能标记为候选值。

调研产物包括受版本管理的测试 CCE、host/golden、运行与分析脚本和记录文档。
记录 CANN/CAModel 版本、SoC、编译参数、提交号、原始日志位置/校验摘要、
关键 PC/instr_id 证据、提取口径及最终配置 diff。体积较大的原始日志可保存在
results，但仅有被忽略的 results 路径不构成可复现交付。

调查状态分为已测、候选、未测。缺失时序项可以 fallback 并 warning，明确
列出覆盖缺口；涉及缺失项的周期结果只能用于功能/资源机制验证，不能据此
校准物理谓词模型的预测精度。未确认的操作数或资源类别不得用 generic
compute 猜测，必须先补充语义或明确拒绝。

## 7. 释放规则

### 7.1 已确定的源码级近似

谓词直接复用当前 vector 的生命周期逻辑，不另行实现“必须同名覆写才能释放”。
输入是较低层的源码/Canonical 数据流，而不是已经完成寄存器分配的机器指令。
真实路径中，编译器会根据活跃区间复用架构寄存器，不同源码变量可能映射到
同一架构槽位；硬件再对这些槽位重命名并回收旧版本。

当前动态 last-use 机制近似的是“编译器寄存器复用 + 硬件释放”的整体效果，
而不是逐事件复刻硬件覆写规则。这样可以降低预测对源码变量命名和显式变量
复用方式的敏感性。不模拟编译器分配、却按源码同名覆写强制保留映射，反而
可能人为拉长占用时间、高估物理寄存器压力。

这是经过 vector 模型校准后沿用的近似，不保证所有场景都与真实编译器分配
严格等价。后续若开发机器指令级回放及硬件覆写模型，应独立研究，不在本次
谓词接入中切换语义。

### 7.2 具体生命周期

1. IFU 按 definition 和动态迭代身份统计全部使用，标记动态最后使用。
2. 最后使用者进入重命名阶段时，先绑定其物理源，再撤销该值的 RAT 映射。
   撤销映射不等于立即释放物理槽位；已入队消费者仍持有对应物理引用。
3. 每个消费者开始执行后，按现有 vector 机制在 start+4 处理源引用释放。
4. 所有引用消耗完成，且满足当前 vector 的 producer/pending、generation、
   未重复释放等安全条件后，回收物理槽位并返回相应资源池的 credit。
5. 未使用目的值的映射保留与回收也沿用 vector 处理，不等待不存在的消费者。

乱序情况下，源码最后使用者不一定最后执行；不能只监听它的 start+4，必须
等待所有消费者的源引用释放事件。没有后续同名覆写的动态值仍可回收。
代码中的当前映射检查可以保留，但应由动态 last-use 撤销映射解除，不能为
predicate 增加等待覆写或 VF 结束的特殊门槛。

日志中的“内部 +3、credit +1”作为观测依据保留。本次使用现有 vector 模型
的 +4 抽象，不新增谓词专用的 3+1 流水，也不再额外叠加 1 cycle。
consumer 的 start 定义保持与 vector 相同；不把 PSTI
最终写 UB 的时刻直接替代其源读取时刻。

### 7.3 实施验收与后续校准

本次必须测试：

- 同构的 vector/predicate 数据流具有一致的引用消耗与回收规则。
- 多消费者乱序执行、重复源引用、跨迭代版本不提前释放或串版本。
- 最后使用后没有同名覆写，仍能按上述规则回收，不能保留至 VF 结束。
- 提前覆写不能释放仍被旧消费者使用的物理值。
- 全有效 mask 与普通谓词遵守同一 last-use 规则，没有永久保留特例。
- 无消费者目的值和 producer pending 的处理与 vector 一致。

以精确 cycle 断言验收，不只断言 `free >= start`。覆写触发路径、不同
consumer 的硬件读取阶段、P0 等仍可后续做定向实验，但不再作为默认释放
规则待定项或本次开发的前置阻塞条件。

## 8. 配置与日志

建议在当前 A5 uarch 配置中增加具有明确单位和范围的字段：

| 字段 | 当前值 | 含义 |
|---|---:|---|
| physical_predicate_registers（新增，暂定名） | 32 | 独立物理谓词容量 |
| consumer_release_start_offset（复用） | 4 | 与 vector 相同的源引用释放偏移 |
| idu_visible_preg_delay（复用语义） | 0 | 当前 vector 的额外 credit 可见延迟，不为谓词额外加 1 |

类型、有效范围、配置应用路径和 Python/Native 支持必须同步，不允许校验通过
但某端忽略。vector/predicate 统一只使用全局 `consumer_release_start_offset=4`，
不按 opcode 区分源引用释放偏移；计数和 credit 返还事件仍独立管理。
暂不新增 predicate_source_read_delay/predicate_idu_credit_delay 两套独立参数。

已删除 `consumer_release_start_offset_by_op` 的实际读取和应用。
Python/Native ParamDB 加载基础 uarch，以及 Canonical override 校验均拒绝该字段，
提示改用全局 `consumer_release_start_offset`。即使旧字段为 null 或空对象也拒绝，
不能静默忽略；其名称仅保留在废弃校验、测试和迁移说明中。

资源池已增加固定合法 ID 集合、已分配集合和统一 `allocate()` 方法。
正常有限资源路径的分配统一经过资源池；释放未知或其他命名空间的 ID 报内部错误，
重复释放和未分配 ID 不会增加 freelist。理论无限资源模式仍保留原有独立路径，
不将其动态生成 ID 返还到有限池。新增耗尽、重复释放、跨池 ID 和容量守恒测试。

日志明确使用 `vector_phys_free`、`predicate_phys_free`、
`src_predicate_phys`、`dst_predicate_phys`，同时保留逻辑 ID、definition、
iteration_path、stream_seq 和 generation。内部释放与 credit 返回分开记录。

增加阻塞原因：predicate credit 不足、predicate source 未 ready。
区分这些原因与 SHQ 满、vector credit 不足；允许记录同时成立的多个原因。

## 9. 实施步骤

1. 归档已确认的 PSET 队列证据，按 EXU01 假设启动功能开发；继续第 6.3 节时序调研，未测项禁止用于精度校准。
2. 固化第 7 节释放规则，补 vector/predicate 生命周期对齐测试，清理 Python per-op 释放遗留路径。
3. 升级 Canonical/Python/Native/JSON 合同及共享 fixture，落实首版拒绝规则；暂不改调度。
4. 改 Catalog、CCE binder 和统一 renameable storage 版本化，让 mask 不再被丢弃。
5. Python 增加分类型资源管理、IDU 原子 credit、统一 ready 和生命周期。
6. Native 实现相同语义，使用同一批 canonical fixture 做逐条动态对齐。
7. 补齐谓词指令参数和 PST/PLD 回放支持，运行端到端与资源压力回归；未支持指令明确拒绝。
8. 更新输入文档、配置说明和可复现测试记录，再讨论基线刷新。

## 10. 验收

### 10.1 单元与动态展开测试

- 普通 vector 与 predicate 的相同编号不冲突；分别分配与回收。
- PSET/VCMP 输出正确版本，VSEL/其他 masked 指令等待正确 producer。
- mask 重定义、alias、重复输入、跨迭代及嵌套 carried 依赖不串版本。
- 所有入口拒绝无 producer 的 PredicateRegister、MODE_MERGING 和 Catalog
  未完整登记的谓词指令；失败不分配资源，诊断有路径与源码位置。
- 语义完整但缺 timing 时正确 warning 并运行，与语义缺失的拒绝行为分别测试。
- 等价的 vector/predicate 循环使用统一数据流算法，不依赖特定 opcode 或名字。
- 零次循环不分配任何动态谓词资源。
- 有 predicate 目的/无 vector 目的的指令正确占用 credit，反之亦然。
- 两类资源不足时不出现部分分配、负 credit 或 freelist 下溢。
- consumer 读后延迟、多消费者、overwrite、dead destination、VF 结束回收。
- 无后续同名覆写时仍按动态 last-use 回收；不得增加强制覆写门槛。
- 32 个资源耗尽可阻塞并最终恢复，不能死锁；测试用小容量池也覆盖边界。

### 10.2 端到端

- 简单 compare-select 例子，camodel 数值 golden 通过，Python/Native 动态依赖一致。
- 本次 8 轮压力 sweep，保留 6/7-mask 架构 spill 边界及 32-mask 物理耗尽证据。
- 对源码输入只对比源码对应的指令流；不能将没有 spill 的源码预测与编译后
  PST/PLD/membar 流直接比较并归因于资源模型。
- 编译后回放要求 Python/Native 的 start/done、credit 和阻塞记录彼此对齐；
  不要求源码级 last-use 近似逐事件复现 camodel 的覆写回收日志。
- 为异常输入保留结构化 diagnostic 和 CCE source location。
- 无谓词指令的原 canonical fixture 周期保持不变。新增真实 PSET/mask 依赖
  导致的旧 CCE 结果变化必须逐项解释，不能直接放宽容差或批量刷新 baseline。
- Python/Native 使用相同全局 +4 释放设置，正式配置不接受 per-op 释放合同。
- PSET 的实测参数与 fallback 项分别标记，缺失项影响的结果不用于预测精度校准。

## 11. 本次暂不做

架构寄存器容量限制、自动寄存器分配和 spill 插入、mask 数值执行、
predicate live-in、MODE_MERGING、其他 SoC 容量推断、跨分支同步以及 Tilesim 对接。
MODE_MERGING 后续必须显式表达 dst.old input 与 dst.new output 后才能开放。
先把当前 A5 的输入合同、
依赖关系和物理资源行为完整贯通。
