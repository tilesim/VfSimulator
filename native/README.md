# Native VF 模拟器核心

本目录包含 VF 模拟器的 C++ 核心实现。

当前约定：

- `configs/*.json` 是模型参数的唯一事实来源。
- C++ 启动时读取并缓存与 Python 相同的 JSON 配置。
- 内部使用轻量 JSON 解析器，避免为基础配置读取引入额外第三方依赖。
- 不在手写 C++ 常量中重复配置值。
- C++ 路径完全对齐前，以 Python 主线行为作为一致性基准。
- 一个加载完成的 `ParamDB` 可以跨预测线程共享：参数 bundle 只读，fallback
  `InstConfig` 按值返回，warning 聚合受互斥保护。forwarding/II 热路径 cache 属于
  每个 `OoOCore`，不写共享 `ParamDB`。

核心模块：

- `ParamDB`：读取 `isa.json`、`uarch.json`、`forwarding.json` 和
  `InitiationInterval.json`。
- `ISATraits`：根据 ISA 元数据判断 load、store 和 compute 行为。
- `CanonicalProgramLowering`：验证并展开 canonical loop，生成动态身份、值生命周期和
  top-block 元数据。
- `IFU`：只消费 `CanonicalProgramLowering` 生成的动态指令流。
- `IDU`：执行 dispatch gate、VLOOP 可见性和信用计数。
- `OOO`：执行 rename、ready、execute 和 retire 主流程。
- `SimulatorRunner`：驱动主循环并输出日志。
- `ControlUnit`：处理全局 membar 的发射、同步放行和退休，参数和验证方式见
  [Membar 时序模型](../docs/membar_timing_model.md)。
- `CanonicalProgramLowering`：处理已验证的 canonical definition、loop-carried
  binding、动态身份和值生命周期标记。
- `CanonicalJsonVfInfoAdapter`：读取并校验语言无关的 canonical JSON v1/v2；谓词寄存器使用 v2。
- 共享配置结构：显式、可移植地描述跨语言配置 schema。

公开运行入口：

本轮 C++ 同步基于主线 `566c398`，包含独立的物理谓词寄存器池、统一 last-use
释放、双输出指令，以及按 Catalog 声明的 `ub_transfer_bytes` 进行共享 UB 带宽仲裁。
`vector_align` 仅维护状态依赖，不分配 vector/predicate 物理寄存器。
新增覆盖包括 PSET、谓词比较/逻辑、MOVVP、PSTU、VLDAS/VLDUS、
VLDS DINTLV_B32 和 VINTLV/VDINTLV。实测值与近似参数的区别见
[谓词参数记录](../docs/predicate_timing_coverage.md)及各指令建模文档。

后续同步来自 `vfinfo-core-api-unification` 的 `bdea3dc` 和 `e641c82`：
增加 read-write operand/VAXPY、PLT，以及 VLDS UNPK_B16 / VSTS PK_B32。
PLT 的 scalar 输入目前不建模 scalar producer 时序；packed 模式的有效载荷为
128B，UB 仲裁预算仍保守取 256B。参数证据和限制见
[PLT 与 packed memory 支持](../docs/plt_packed_memory_support.md)。

- `runCanonicalVfInfo()`：唯一正式预测入口，跳过 legacy value lowering、vreg
  live-range normalization 和 single-super-iteration rewriting。
- `loadCanonicalJsonVfInfo()`：Native JSON runner 的唯一输入解析入口。

旧 `VfInfo.cpp`、`ProgramAnalysis`、`ProgramFlatten`、
`ProgramCanonicalization`、`LegacyVfInfoAdapter` 和 `JsonVfInfoAdapter` 编译到独立的
`vfsim::native_legacy` 静态库，只供离线迁移工具和历史测试使用。编译器正式接入只
链接 `vfsim::native_core`；`SimulatorRunner.h` 不再声明 `runLegacyVfInfo()` 或
`runVfInfo()`。

顶层调度 block 由完整 context 顺序决定：每个顶层 loop 开始一个新 block；该 loop
之后、下一个顶层 loop 之前的普通指令和 `Membar` 属于当前 block。因此 loop epilogue
中的 `VSTAS` 会在开启下一个 VLOOP 前完成 IDU dispatch。`count=0` 的顶层 loop 会
记录为空 block，IDU 初始化和块切换时显式跳过它，直接开启下一个包含普通动态指令的
block。
