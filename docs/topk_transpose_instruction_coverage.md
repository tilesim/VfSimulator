# TopK / Transpose 指令补齐记录（2026-10-18）

## 当前结论

已完成 VFSim 0.3.2 wheel 与 TileSim 接入。TopK 现在使用 40 条 legacy UB 指令和 26 次独立 VF 调用计时，预测 15698 cycles，对照完整 PTO 源码 CAModel 的 15784 cycles，误差 -0.545%。Transpose 已执行真实 Gather/Scatter。**这不代表原来的 1952 GT 精度已经修复，也不代表所有 forwarding/控制时序已经完全校准。**

检查时，工作分支 `VfSim-tilesim` 从 `bb9216f` 快进到 `origin/VfSim-tilesim` 的 `8dafdd0`。上游仅新增 MOVP；检索更新后的 18 个 origin 远端引用，没有发现 VGATHER2、VSCATTER、VBITSORT、VMRGSORT4、VSQZ、VSTUR、VSTAR 的实现。

本次实现位于本地未提交修改中；没有推送、发布或改动他人的服务器任务。

## 已实现的范围

| 指令 / 形式 | 新增能力 | 实测结果（cycles） |
|---|---|---:|
| VBITSORT / VBS32，FP32 | 独立 legacy UB VEC 计时 API，保留硬件 repeat 字段 | repeat 1/2/8/32：51/67/163/547 |
| VMRGSORT4 / VMS4V2，FP32 | 精确匹配 TopK 所用的三种列表长度与 repeat 配置 | 4×32，repeat=8：552；4×128，repeat=2：552；2×10，repeat=1：48 |
| VGATHER2，FP16 + uint16 索引 | 真实索引、谓词依赖；按步长和有效元素数选延迟；目标模式多周期 LSU 占用 | stride=128、128 lanes：147 |
| VSCATTER，FP16 + uint16 索引 | 保留数据、索引、谓词依赖；目标模式多周期 LSU 占用 | stride=64、128 lanes：651 |
| VCI.int16 | 有符号 16 位索引生成形式 | 7 |
| VMINS.uint16 / VMULS.uint16 / VADDS.uint16 | 无符号 16 位索引计算形式 | 6 / 8 / 6 |
| PINTLV_B32 | 两个谓词输入、两个谓词输出；支持 PAT_ALLF 设置 | 11 |
| VSQZ.fp32，MODE_STORED | 显式向量、谓词依赖 | 18 |
| VSTUR.fp32 | 对齐状态 append；显式选中元素范围与 POST_UPDATE 字节数 | 9 |
| VSTAR.fp32 | 对齐状态 consume；等待同一状态的前序 VSTUR | 8 |

`predict_legacy_ub_instruction` 不把 UB 排序指令当成普通寄存器 VF ALU。VfSimProgram 中出现这些指令会明确报错，避免得到错误的默认 ALU 延迟。

VBITSORT 的重复次数规律为 `35 + 16 × repeat`，1/2/32 用于建立规律，8 用于检查该规律。VMRGSORT4 仅接受已测的三种配置，其他配置明确拒绝，不外推。

Gather/Scatter 目前测量了 element stride=1/2/16/64/128、有效连续前缀 32/128 lanes，共 20 组。未知步长、未知 lane 数、非 FP16 或非 uint16 索引明确拒绝。调用方负责提供与真实索引和谓词一致的 `index_stride_elements` / `active_lanes`。

已测重复吞吐为 Gather(stride=128, lanes=128) 的 128 cycles 和 Scatter(stride=64, lanes=128) 的 643 cycles。其他已测单指令模式尚无重复吞吐测量，使用串行保守值，并返回 `unmeasured_indexed_throughput` warning。未测的 forwarding / II 组合保留 warning，不伪装成已校准。

## 修复的引擎问题

原 `_load_ready_cycle` 只考虑 LSU 就绪及 load-align 状态，不检查寄存器生产者。因此，即使补入 Gather 的索引操作数，它仍可能在索引或谓词就绪前执行。现在加载指令同时等待寄存器依赖；普通 VLDS 没有这些寄存器输入，维持原行为。

索引访存的不同延迟使用 ParamDB 所属的独立 profile，保留 profile 所有权与缓存隔离；没有绕过参数数据库的校验。

## 测量证据与环境

服务器使用用户提供的 `liteserver-remote/SKILL.md` 连接。实验独立位于 `/home/wxk/a5_topk_transpose_20261018`。

- CANN：`/usr/local/Ascend/cann-9.2.0-beta.1`；ccec clang 15.0.5；dav-c310-vec；O2。
- CAModel 库由 `Ascend950PR_9599` 别名定位到 dav_3510；运行日志的 `halGetSocVersion` 报告 `Ascend950PR_9589`。不能把这次实验称为 1952 真机校准。
- 测量取指令 popped/start 与 completion，不使用 host 运行时间。
- 32 个有效 case 的完整输出和保护区通过独立检查。另有 3 个早期归并探针因标量初始化未等待 MTE2 而输出失败，完整保留，但**排除出校准数据**；补齐 MTE2→S 与 S→V 同步后，三种归并配置均通过。
- 本地原始归档：`results/a5_topk_transpose_20261018/final_evidence.tar.gz`。含 CCE 源码、编译日志、输入输出、逐指令日志、编译器版本、ELF 哈希与运行日志。
- 汇总：同目录 `measurements.json`，记录 35 次尝试及其中 32 次有效结果。

## 完整 Transpose 序列一致性检查

比较范围均为第一条有效 VF 指令发出至最后一条有效指令完成；排除 scalar、MTE 和 RV_SEND。模型自身的 VF startup/drain 不混入该范围。

| FP16 源 tile | CAModel 序列跨度 | VFSim 序列跨度 | 相对误差 |
|---|---:|---:|---:|
| 64×128，行 Scatter 路径 | 41097 | 41176 | +0.192% |
| 128×128，列 Gather 路径 | 16542 | 16425 | -0.707% |

上述完整序列也用于观察 LSU 吞吐，因此这是**同一环境中的组合一致性检查，不是独立 GT 验收精度**。详细结果和模型轨迹位于 `model_validation.json`、`model_validation/`。

这些耗时与原 1952 GT 的约 1556 / 2025 cycles 显著不同。仍需核对原 GT 的源码版本、编译产物和测量范围，不能用本次同环境误差宣称原 GT 已修好，也不能直接把新测量与旧 GT 拟合。

## 验证状态

- 最初新增能力与相关 API、谓词、缓存、包入口的聚焦回归：85 项通过；后续接入回归见下文。
- VFSim 全量 pytest：371 项通过、7 项跳过。原有五项失败已实际修复：两份缺失的 catalog/schema 声明由原仓库生成器生成；三处 CLI 测试改用 `sys.executable` 调用当前解释器，避免 Windows 下 `python3` 别名返回 9009。没有跳过这些测试。后续 160-byte copy 新增聚焦用例也通过。
- 原分支缺失 smoke baseline。现从冻结的上游 `8dafdd0` 原始代码单独运行并建立对照，再比较当前代码：smoke 全部通过，各例 cycles 与上游相同。没有拿修改后的代码重置基线来掩盖退化。证据位于 `results/a5_topk_transpose_20261018/current_smoke/compare_summary.json`。
- 包副本同步检查通过；0.3.1 wheel 在独立安装目录中运行，真实导入 wheel 的代码，并执行 Gather、VBITSORT、VMRGSORT4 API。
- 当前实现覆盖 Python mainline / TileSim typed API 路径；尚未向 C++ 后端移植。CCE 前端尚不能自动推导数据相关的 VSTUR 增量或索引模式，需由 typed API 调用方提供明确元数据。

## 继续工作的边界

1. 已完成：TileSim 捆绑并安装 0.3.2；58 个已安装代码/配置文件与 wheel 字节一致；Transpose 原来的普通 VLDS/VSTS fallback 已消失，保留索引、谓词、仿射地址，并在公开 API 返回 VFSim warnings。
2. TopK legacy/VF/160-byte copy 混合计时已接入，保留 26 个 VF 边界；此具体源码由完整 trace 证实串行。仍不把该求和方法视为通用跨域调度模型，控制/转发 warning 尚未完全闭合。
3. 核对原 1952 GT 的编译路径，再决定可比较的精度更新。原 HTML 目前仍是旧报告。

## 完整 PTO TopK 新证据

使用当前 PTO 头文件编译实际 TSORT32/TMRGSORT/TMOV/TGATHER 调用，而不是仅手写对应的几条指令。完整 [8,1024] / FP32 / k10 kernel 在 CAModel 上运行成功，独立校验了 80 个逻辑值、80 个索引、1024 字节保护区。索引按相同值的实际来源核对，不强行规定并列值的索引顺序。

实测动态指令：8 VBS32、24 VMS4V2、26 VF 调用、8 MOV_UB_TO_UB；VF 内还包含 536 PLT、536 VLD、520 VST、16 VSQZ、16 VSTUR、2 VSTAR、2 SPRCLR，和源码结构一致。VEC busy sum、busy union、首尾跨度均为 **15784 cycles**：此具体路径没有 VEC 调用重叠，也没有 VEC 空闲间隔。

这为按源码边界组合 legacy 与 VF 计时提供了依据；不是默认所有混合 kernel 都可串行相加。余数 160-byte UB copy 的八次执行均为 30 cycles，已加入受限的源码计时 API，并随 0.3.2 wheel 与混合 TopK 接入一起验证。

原始证据：工作目录 `repro/vfsim_full_topk_20261018/topk_pto_evidence.tar.gz`；完整验证与分域计时 `runs/topk_pto/topk_validation.json`。主机 runner 本身未内置此 TopK golden；上述检查由独立分析器执行，未把其“skipped”提示误当作通过。

后续顺序遵循用户新增要求：先完成本阶段，再覆盖其他 TileOp 类型，优先逐元素；每类收官后提供服务器新采、原 GT 两份同格式的逐 case 全 pipe HTML 色阶报告。

## 2026-10-19 混合计时接入

- 新增 `predict_serial_vec`，调用方必须明确给出串行证据；不会把普通混合 kernel 自动假定为串行。此接口求 VEC execution sum，排除 scalar、DMA、queue wait 与 dispatch gap。26 次 VF 各自重新计算 startup/drain，不展平。
- `SPRCLR spr=74` 是无寄存器输出的 compute/state 指令，延迟 8 cycles。后续 VSQZ 等待 reset；再次 reset 等待旧用户。未测 SPR_AR update forwarding 保留 warning，不伪装完整状态模拟。
- 160-byte UB copy 仅允许 repeat=1、fp32、单次 160-byte 已测配置，30 cycles；其他大小明确拒绝。
- TileSim TopK 公开 API：15698 cycles。原 GT 单 tile 19028 cycles，对应 -17.50%；原 GT 平台、源码和范围仍未核实。
- 完整 PTO 新测对照中，VF move32 预测81/实测84，move1预测50/实测52，extract预测85/实测94；还包括少量 native dispatch 的4 cycles。没有用 TopK 总耗时硬调系数。部分 copy 校准与吞吐来自组合原始 trace，所以此结果是组合一致性检查，不是完全独立的 held-out 精度。
- 当前模型仍标记 approximation、timing_complete=false，明确披露 scalar control / forwarding / 原 GT identity 等限制。物理输出为8×16，逻辑输出8×10。
- VFSim 正式 tests/ 目录：381 passed、7 skipped、198 subtests。根目录无边界搜集会同时收集隔离的上游 baseline 副本，产生同名模块冲突，因此明确运行 `python -m pytest -q tests`。
- TileSim 要求的 API/结构回归及新增混合边界/单chunk死谓词测试：67 passed；示例通过。原冻结上游 smoke 对照全部通过，各例 cycle 未变。
- wheel SHA256：bc43ffb6c330b6cbaef5e2eae73a06ea050ee6ec293f35ee04939a6197806b23；安装路径58个代码/配置文件与捆绑 wheel 字节一致。
- 阶段报告位于工作目录 `repro/topk_transpose_progress_20261019/`，原GT184case×7pipe，新采集35探针尝试+3个完整PTO算子各7pipe；未测/缺失保留灰格，失败探针不参与精度。
- 本阶段源码和服务器对照已推进，原 GT 身份和剩余细粒度时序 warning 尚未闭合；未开始其他算子类别。

## 直接 PTO TTRANS 编译验证（2026-10-19）

使用当前 PTO 头文件实际调用 TTRANS，两个 kernel 均执行 exit0。逐位校验 FP16 的存储比特，避免 NaN 浮点比较；64×128 检查8192元素与16384字节保护区，128×128检查16384元素（完整覆盖32768字节，未虚构额外保护区）。所有动态数据指令种类/数量与 typed VF 一致。

| 源 tile | 完整 CAModel VF execution | TileSim/VFSim | 相对误差 |
|---|---:|---:|---:|
| 64×128 | 41129 | 41212 | +0.2018% |
| 128×128 | 16577 | 16462 | -0.6937% |

范围使用 VF 的 vf_real_execute_time，含 entry/exit；排除 scalar/MTE，与前面的手写 body span 表不同。这两次实际编译结果未用于调参。CANN O2 删除 repeat_count=1 时未消费的 full predicate；模型保留源码 marker，但不在可执行 VF 中计入它。两次原始 source/ELF/编译与运行日志/input/output 归档、逐项哈希、canonical 程序和校验器位于工作目录 repro/vfsim_full_transpose_20261019。

TopK 的全部66个 native/VF边界也已逐一与 trace 排序核对：VBS4376/4376、merge9216/9220、TMOV32总1296/1344、copy240/240、TMOV1总400/416、extract170/188（预测/测量），合计残差-86。原 GT 和服务器新测仍分开，阶段 HTML 为184case×7pipe、38次尝试×7pipe。
