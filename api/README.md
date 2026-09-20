# API 层

VfSimulator 的 Python、C++ 和 JSON 正式输入合同统一为
`CanonicalVfInfo v1`。旧 `VFInfo` 和旧 JSON trace 不再进入正式预测 API。

## 正式入口

- `InputAPI.load_json(path)`：按
  `api/frontend/canonical_vf_info_v1.schema.json` 加载并校验 canonical JSON。
- `InputAPI.load_cce(path, kernel_name=None, loop_params=None)`：解析 CCE
  `__VEC_SCOPE__` 并直接返回 `CanonicalVfInfo`。
- `InputAPI.new_builder(...)`：程序化构造 canonical 对象。
- `CoreVfCostModel.predict_vf_cycles(vf_info)`：唯一 Python 预测方法，参数必须为
  `CanonicalVfInfo`。
- `CoreVfCostModel.run_vf_info(vf_info)`：返回完整模拟结果。
- `CoreVfCostModel.run_payload(payload)`：只接受 CanonicalVfInfo v1 的 JSON 对象，
  不猜测旧格式。
- C++ `runCanonicalVfInfo(vf_info, db)`：唯一 Native 预测入口。
- C++ `loadCanonicalJsonVfInfo(path)`：Native canonical JSON 入口。

`CoreVfCostModel` 的默认精度参数命名为 `fallback_dtype`，不是 VF 全局精度。
时序查询优先使用每条指令的 `form`；输入输出值保留各自 `dtype`。
旧 `CoreVfCostModel(dtype=...)` 关键字仍兼容，内部 lowering payload 改用
`fallback_dtype`，不改变公开 Canonical JSON schema。当前 lowering 的兜底值仍为
`fp32`，与此次清理前一致；它不覆盖明确给出的指令 form。

已删除“寄存器值数量乘 unroll 超过物理寄存器数”的静态风险 warning。
架构寄存器溢出预警尚未实现；物理寄存器 credit 和实际阻塞的仿真保持不变。

Python builder 示例：

```python
builder = InputAPI.new_builder(params={"N": 4})
builder.register_storage_object("ub.input", storage=StorageKind.UB)
builder.register_value(
    "input.0",
    logical_id="input",
    storage=StorageKind.UB,
    dtype="fp32",
    storage_object_id="ub.input",
)
with builder.loop("loop.0", induction=InductionVariable("i"), count="N"):
    ...
vf_info = builder.build()
cycles = CoreVfCostModel().predict_vf_cycles(vf_info)
```

## Canonical 契约

- `definition_id` 表示一次值定义，`logical_id` 表示源程序中的逻辑值。
- instruction 显式携带 `instruction_id`、`opcode`、`form`、
  `instruction_class` 和带 role 的 operands。
- input value 的 `producer_node_id` 是寄存器 DATA 依赖的唯一事实来源。
  `dependencies` 只表达额外 memory/control ordering。
- UB 使用稳定的 `storage_object_id`。operand 可携带 affine memory access；当前
  Core 不自动根据 UB 地址建立依赖，UB 顺序由显式 Membar 控制。
- loop 显式描述 induction、count、unroll 和 entry/back-edge/exit definitions。
- 未知但语义完整的 opcode 可以通过 validator，timing 缺失由 ParamDB 使用默认值
  并记录 warning。
- Python/C++ 必须遵循相同的 int64、scalar、枚举和未知字段约束。

canonical JSON 使用可选依赖 `jsonschema`。该依赖只在调用 `InputAPI.load_json()`
或 `CanonicalJsonVfInfoAdapter` 时延迟加载。

## CCE 前端

CCE parser 使用 `api/frontend/adapter_ir.py` 中的私有 `AdapterProgram` 表示尚未
SSA 版本化的源语言事件。该结构不从 `api` 导出，也不是外部合同。
`ValueVersioningPass` 将它转换为 `CanonicalVfInfo`：

```text
CCE __VEC_SCOPE__
  -> private AdapterProgram
  -> ValueVersioningPass
  -> CanonicalVfInfo validation
  -> CoreLoweringPass
  -> Core
```

Catalog 是已知 opcode 的语义事实来源，声明 opcode alias、instruction class、
semantic form、CCE operand signature、call variants 和 specialization。timing 参数仍
只由 `configs/isa.json`、`forwarding.json` 和
`InitiationInterval.json` 决定。

CCE 的 vector、predicate、scalar 和 UB pointer 使用词法作用域。寄存器赋值
`b = a` 在私有 IR 中表示零周期 alias，并在赋值点绑定 `a` 的当前 definition。
普通 offset 与 `POST_UPDATE` 分开表达；affine offset 只支持常量、变量、加减和
常量乘变量。

## Legacy 迁移

旧格式只保留为离线转换能力：

```bash
python3 tools/convert_legacy_vfinfo.py old.json canonical.json
python3 tools/convert_legacy_vfinfo.py old.json canonical.json --target cpp
```

Python 的 `api.vf_info`、`api.json_adapter` 和
`api.frontend.legacy_vf_info_adapter` 只供该转换流程与历史测试使用，不由
`InputAPI`、`VfCostModel` 或 `CoreVfCostModel` 导出。Native 的旧转换代码位于独立
`vfsim::native_legacy` 静态库；正式编译器接入只链接 `vfsim::native_core`。

`regression_suite/inputs/canonical` 保存正式回归使用的 canonical fixture，Python
和 Native runner 直接读取这些文件。`regression_suite/inputs/json` 只保留为迁移
工具的历史输入。

`VFtest/canonical` 保存可直接传给 `main.py --trace` 的示例；`VFtest` 根目录中的旧
program JSON 仅用于离线转换和历史 optimizer。

`--target cpp` 会移除 Python-only 的
`canonical_dynamic_instruction_limit`。Native 对 Python-only 或未知 uarch 字段明确
报错，不会静默忽略。

## Core 边界

`CoreLoweringPass` 是 canonical 到内部 JSON-like payload 的唯一入口。它保留静态
instruction ID、definition ID、动态 iteration path、loop-carried binding、值实例
last-use/keep 标记和稳定 UB object。旧 vreg normalization、字符串 `_laneN` 改写和
旧 `VFInfoLowerer` 不再位于正式流程，`VFInfoLowerer` 已删除。

当前支持可整除的 innermost `unroll>1`；非 innermost unroll 和不可整除 batch 会
明确拒绝。含 Membar 的 innermost unroll 保守回退到 1 并记录 warning。

## POST_UPDATE 地址状态

Canonical `MemoryAccess` 可提供 `address_state_id`、`update_mode`（`none` 或
`post_update`）和 `post_update_delta_bytes`（affine expression）。更新必须同时
提供状态与增量；零增量仍是一条更新事件。普通访问也应提供所读取的状态 ID，
以便识别前序更新。缺省字段的旧输入保持原行为。

Python/Native 在 IDU 顺序 dispatch 时检查地址 scoreboard。成功分发的更新
使该状态在 `dispatch_cycle + idu_post_update_ready_latency` 就绪，默认间隔 1。
队头未就绪时，后面的独立指令也不能绕过；已经通过 IDU 的同指针指令允许在
LSU 资源满足时双发，不再等待前一条 LSU start。地址状态不占 vector preg，
也不表示 UB 数据依赖；数据内存同步仍由 Membar 控制。
`lsu_post_update_ready_latency` 已废弃并明确报迁移错误，不自动映射到新参数。
`idu_to_ooo.json` 和 `idu_address_blocked.json` 记录地址 producer 的 dispatch
时间与 ready_cycle；`sim_history.json` 不再输出旧 start-based 地址事件边。
CCE 前端依据 Catalog 中的 `post_update_delta` 规则生成字节增量：普通
`VLDS/VSTS/VSTUS/VSTAS` 将显式元素步长乘以指针元素宽度；`VSSTB` 将复合
配置的低 16 位解释为 32 字节 block 数，因此 `(129 << 16) | 1` 的更新量为
32 字节。高 16 位 block stride 不属于指针更新量。
完整支持范围、限制及复现步骤见
[POST_UPDATE 开发与验证记录](../docs/post_update_address_dependency_plan.md)。
