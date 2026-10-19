---
name: vfsim-instruction-benchmark
description: 通过 CCE/CAModel benchmark 测量和校准重构版 VFSim 的指令 latency、forwarding、II、执行单元分类及端口范围。用于指令参数获取、补测和时序证据分析。
---

# VFSim 指令参数标定

为指定 opcode/form 设计可复现的 CCE benchmark，通过数值校验和动态指令日志取得参数，形成测量记录及配置修改。测量范围同时记录 dtype、mode、mask、源操作数位置和目标架构。

## 确认工作目录与参数合同

先区分 **模型目录**（待校准的重构版 VFSim）和 **实验目录**（具有 CCE/CAModel 工具的检出）。两者可以相同。读取各自分支和工作区状态；实验工具缺失时，定位已有完整检出或记录缺失项。纯 C++ 检出可复用外部实验目录生成证据。

在模型目录核对以下文件；按实际内容确定参数键和行为：

- `configs/instruction_catalog.json`：指令签名、form、操作数角色、访存模式及地址语义。
- `configs/isa.json`、`configs/forwarding.json`、`configs/InitiationInterval.json`。
- Python 的 `core/params/{types,database}.py`、`core/uarch/{exu,lsu,register_bank}.py`；C++ 对应文件在 `native/core/` 或纯 C++ 检出的 `core/`。
- 存在时读取 `docs/configuration.md`、`docs/instruction_timing.md`、`docs/validation.md` 中相关章节。

按下表建立此次实验的目标字段：

| 目标 | 口径 | 配置位置 |
| --- | --- | --- |
| 指令 latency | 同一动态指令完成周期减 popped/issue 周期 | `isa.json` 的 `instructions.OP.forms.FORM.latency` |
| FU 分类 | 模型中的 ALU/SFU 功能分类，结合已有分类与执行证据 | 同一 form 的 `EXU` |
| 可用计算端口 | 目标指令能够使用的 EXU 端口集合 | 同一 form 的 `dispatch_exu` |
| forwarding | 真实 producer→consumer 的最早可用发射间隔 | `forwarding.json` 的 `forwarding["OP.form"]["OP.form"]` |
| II | 同端口、源已就绪的相邻计算指令的最小发射间隔 | `InitiationInterval.json` 的 `InitiationInterval["OP.form"]["OP.form"]` |

LOAD/STORE 分别测量自身的 `latency`。LOAD→compute、compute→STORE 的依赖间隔写入对应 forwarding 指令对。`op_class`、`dtype`、签名及 form 来自语义定义，结合编译产物核对。

**字段边界：** `pipeline_startup_cost`、`pipeline_drain_cost`、`data_load_cost`、`data_store_cost`、`throughput` 不作为此 skill 的标定输出；配置中残留的这些键也不构成继续写入的依据。VF 整体开销使用 `isa.json.defaults.vf_startup_cost/vf_drain_cost`，仅在任务包含整体启动/排空校准且具备对应边界事件时修改。资源容量、循环控制和屏障参数需要专门结构实验。

## 工具与测量记录

`VFtest/cce/` 保存 VFSim 测试使用的 CCE/DSL 源码。本 skill 的 `scripts/` 提供实验工具，`assets/` 保存 host 程序；生成的源码、二进制和日志放入独立运行目录。

- [run.py](scripts/run.py)：复制、谓词、比较、VAXPY 和交织探针的生成、运行与数值校验；[compute_sweep.py](scripts/compute_sweep.py) 批量运行计算探针。
- [memory_probe.py](scripts/memory_probe.py)：特殊访存探针与 golden；[archive_memory.py](scripts/archive_memory.py) 归档运行证据。
- [forwarding_source.py](scripts/forwarding_source.py)：PSET 间隔探针生成；[analyze_forwarding.py](scripts/analyze_forwarding.py) 分析并归档依赖间隔。
- [pressure.py](scripts/pressure.py)：谓词压力探针；[analyze_pressure.py](scripts/analyze_pressure.py) 分析资源和 spill 证据。
- [host.cpp](assets/host.cpp)：探针的 CAModel host 程序。

复用已有结论时，按目标读取 [谓词数值验证](references/predicate-validation.md)、[谓词压力实验](references/predicate-pressure.md)、[VAXPY FP32 测量](references/vaxpy-timing.md)、
[谓词 spill](references/predicate-spill.md) 或 [MOVP 与 VMOV 转发](references/movp-timing.md)。

## 设计与执行实验

先阅读 [实验方法与配置示例](references/measurement.md)，选择单指令、依赖链、独立流、间隔扫描或端口竞争实验。优先复用实验目录中已有且符合目标的探针。

每次固定 CANN/编译器版本、SoC、core arch、完整编译命令、fusion/misched 设置、unroll、循环次数、输入和 golden。已有 A5 样本使用 CANN `9.0.0-beta.1`、`dav-c310-vec`、`Ascend950PR_9599`；运行时读取实际环境，不把该组合视为所有目标架构的默认值。

保持全部目标结果可观察，检查编译后 RV 指令数量、寄存器、谓词、隐式读写和实际依赖。对于相同指令的独立流，使用不同输入或立即数并分别存储结果，检查合并、删除和重排情况。两个输入可以使用相同向量的最小模板仅适用于语义允许且结果有辨识度的指令。

先完成独立 golden 校验，再将时序作为参数证据。多输出全部校验；部分 mask 检查有效区与保留区；特殊 STORE 检查写入字节和哨兵区。编译失败、运行失败或数值失败时保留诊断产物并修正该用例；失败样本不能产生已确认参数。环境不可用时交付探针设计与运行方法，并明确尚未获得实测数据。

## 分析日志

- `core0.veccore0.instr_popped_log.dump` 提供 issue 起点，`core0.veccore0.instr_log.dump` 提供完成事件。以同一运行、core、veccore、VF 调用和动态 ID 关联，核对 opcode。
- `core0.veccore0.rvec.EXU.dump` 用于检查端口和执行事件；必要时查看 IDU/ISU/LSU 日志。EXU PERF 起点可能比 popped 晚 1 cycle；分析 II 时核对两个口径的一致性，统一转换后再写入模型。
- forwarding 核对具体源寄存器/谓词或 align-state 的 producer，排除其他源等待、循环开放、端口冲突和队列阻塞。间隔扫描到源就绪与发射间隔稳定后确认；有额外等待的单个差值只能作为受限观测。
- II 按同端口的相邻实际发射序列分组，排除 RAW、其他在途瓶颈和中间指令。双端口总吞吐不能直接当作同端口 II；相反方向的指令对分别测量。
- 记录样本数量、差值分布、隔离条件和可复现的最小有效值。出现不同值时解释影响因素，保留差异及适用范围。

端口样本同时覆盖 EXU0/EXU1 可支持 `EXU01`；单次只出现 EXU0 时继续做竞争与独立流实验。`EXU0_ONLY` 需要额外证据；尚未隔离的端口限制可作为明确标注的保守配置。`EXU=ALU/SFU` 与端口编号分别记录。

## 写入与验证

使用 form 精确键，先核对 schema 和实际查询结果。为已隔离的指令对新增显式值；将 `*` forwarding 限定为具有相应覆盖证据的默认规则。分别标注 **实测**、**近似**、**未覆盖**，保留兼容 form 和缺失参数回退产生的 warning。

参数更新后创建新的模型实例加载配置快照，运行相关指令测试和既有 smoke 回归。项目同时维护 Python/C++ 时，对同一 Canonical 输入核对 issue/done 与周期；两个分支的修改范围按用户任务确定。参数修改引起的基线差异逐项解释后，再按任务要求更新基线。

交付内容应包含：

- 探针源码、输入、独立 golden、输出校验及完整编译/运行命令。
- 原始日志、按动态 ID 配对的事件表、参数提取方法和样本统计。
- opcode/form、dtype/mode/mask、依赖源位置、端口、环境和适用范围。
- 配置文件精确键、原值与建议值、证据类型、回归结果及剩余测量缺口。

把大体积运行产物放入独立实验目录，记录路径及复现命令；按仓库约定保存源码、结果摘要和确认的配置。提交、推送及跨分支同步按用户授权范围执行。
