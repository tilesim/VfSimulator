# DV100 VOR/VCI 最小参数接入

仅针对 Ascend950PR_9599 / dav-3510 / DV100 的已验证形式。证据源为
`results/ordinary_extra_stage1_20260920/batch1_tmp_retry`、`batch2` 的
`validation.json`、`per_id.json` 与原始日志；计时为同一动态 ID 的
`instr_popped_log` 到 `instr_log` 完成。CAModel 样本不等同硅片实测。

| 参数 | 回填 | 对应证据 |
| --- | --- | --- |
| VOR.b32 latency | 6 | batch2 ID135-139、141-143，512/512 数值通过，均为 6 周期 |
| VCI.int32 latency | 7 | batch2 ID156-163，512/512 数值通过，均为 7 周期 |
| VLDS.uint32 -> VOR.b32 | 6 | batch1_tmp_retry ID75/76 -> 77；VOR 分区 64/64 通过 |
| VOR.b32 -> VSTS.uint32 | 4 | batch1_tmp_retry ID77 -> 78；VOR 分区 64/64 通过 |
| VCI.int32 -> VSTS.int32 | 5 | batch1_tmp_retry ID81 -> 82；VCI 分区 64/64 通过 |
| VCI.int32 self-II | 2 | batch2 同 EXU 独立指令 ID156 -> 158 -> 160 -> 162、ID157 -> 159 -> 161 -> 163；后继进入 EXQ 后有 `VALU_THRPUT_CFLT`，无 RAW |

batch1_tmp_retry **整批**因 BF16 ODD 64/64 失败，故只采用独立通过的
VOR/VCI 分区及对应 ID；BF16 失败边、带 store 端口阻塞的候选均未采用。
batch2 的 VOR 输入逐周期唤醒，不能从同 EXU 2 周期间隔认定 self-II；
`VOR.b32 -> VOR.b32` 保持未配置，查询仍产生 `missing_ii_pair` warning。

VOR 的 CCE 调用为 `vor(vector_u32 &dst, vector_u32 a, vector_u32 b,
mask, MODE_ZEROING)`，输出/输入类型均为 `uint32`，日志 form 为 B32；
Catalog 使用该指令的 `fixed_form=b32`，未修改全局 dtype 映射。
VCI 调用为 `vci(vector_s32 &dst, int32_t index, INC_ORDER)`，无 predicate；
头文件允许省略第三参，默认 `INC_ORDER`。归档中的
`vci(y, (int32_t)-17, INC_ORDER)` 已验证可由 CCE 前端解析为 `VCI.int32`。
Catalog 为真实 load/store 类型增加 `VLDS.uint32`、`VSTS.uint32`、
`VSTS.int32` 语义形式，但未测其独立 ISA latency，因此未添加对应 DV100
ISA form；ParamDB 查询会给 `unsupported_isa_form` warning，而非声称已覆盖。

batch2 的 VOR/VCI 均有数值通过的 EXU0 和 EXU1 样本。
ISA 中 `eligible_exus=[EXU0,EXU1]` 来自日志；`EXU=ALU` 是根据按位 OR /
索引生成语义及 VCI 的 VALU 吞吐冲突作出的**功能类型推定**，不是日志
直接证明的独立功能单元分类。未回填其他 SoC、形式或未测时序字段。

本分支为单 SoC A5 版本，使用根目录 configs 和 dispatch_exu=EXU01；DV121 隔离测试仅在 MultiSoC 分支运行。
