# A5 谓词计算指令参数覆盖

## PSET 暂定合同（2026-09-24）

| 参数 | PSET_B32 | PSET_B16 / PSET_B8 |
|---|---|---|
| latency | 6，发射到完成，已实测 | 6，沿用B32，未实测 |
| forwarding | VDUP/VADD为2，已隔离实测；其他消费者暂设2 | 统一2，未实测 |
| 单元 | ALU / EXU01，EXU1有日志；双端口按A5架构规则推定 | 同B32，未实测 |
| self-II | 暂按缺省1，未实测 | 同B32，未实测 |
| 混合II | 沿用latency差值近似，未实测 | 同B32，未实测 |

不修改 `initial_top_block_vloop_start_cycle=19`、`vf_startup_cost=23` 或启动门槛。
本轮假设predicate/vector写回存在冲突，沿用现有缺省II：
`latency(prev)-latency(cur)==1` 时II=2，否则II=1，显式pair仍优先。
例如 VADD.fp32(7) -> PSET(6) 的缺省II=2，反向为1。
这只是相邻指令以1-cycle间隔发射的冲突近似，不是所有在途指令的写回预约表，
不能声称覆盖了任意非相邻指令或已有停顿后发生的完成冲突。

配置为 `configs/isa.json` 的三条PSET及 `configs/forwarding.json` 的
`PSET_B*.b* -> "*": 2`。新增通用producer-form默认consumer项，Python/Native均消费，
显式pair（含既有兼容查找）优先；仅当无pair时取该producer的 `*`。
不为每个新consumer复制一份PSET配置，也不在调度器中增加PSET特判。
未知语义仍由Canonical/Catalog拒绝，时序默认值不改变这一规则。
缺失II仍保留warning，不能据此声称II已经校准。

证据与参数口径见 `pset_vadd_i16_timing_probe.md` 和 `pset_forwarding_probe.md`。
计算消费者的SHQ ready已通过 `forwarding-1` 适配EXQ转移，配置2不再额外加成3。

## 第二类计算指令清单

依据用户提供的 `950PR_HiVM_adapter修复与精度复测报告_20260920.md` 第6.1节：
重点为VCMP_NE/GT/GE/LT、PAND、POR、MOVVP；另补齐EQ/LE及VCMPS六种比较，
用VSEL消费谓词验证结果。PSET本身另行覆盖。
报告没有把PSTU视作普通计算：PSTU及VLDAS/VLDUS/VLDSX2等搬运/状态语义留到最后。

首批测试固定FP32比较、全有效mask、MODE_ZEROING，无loop和unroll。
含大于、小于、等于的输入；VSEL把谓词结果转换成可校验FP32输出。
不将未完成操作数合同的指令直接加入Catalog/generic compute fallback。

## 首批测量

| 指令 | 样本数 | 发射到完成 | 数值验证 |
|---|---:|---:|---|
| VCMP_EQ/NE/GT/GE/LT/LE.fp32 | 6 | 均6 | 全部通过 |
| VCMPS_EQ/NE/GT/GE/LT/LE.fp32 | 6 | 均6 | 全部通过 |
| VSEL.fp32 | 随上述探针 | 6 | 全部通过 |
| PAND、POR | 2 | 均7 | 全部通过 |
| MOVVP.B32，part=0 | 2（全零/全一） | 均16 | 全部通过 |

这里是单指令观测latency，不是隔离测得的forwarding或self-II。
PAND使用GT与LT交集、POR使用两者并集；尚须增加非空交集及更丰富pattern。
比较输入目前为有限普通FP32，不含NaN/Inf或非全有效mask。
本轮生产配置只落PSET，其他测量先保留证据，后续完善合同及参数矩阵后落参。
共16个探针，归档于 `results/predicate_compute_benchmark_20260924/`，
`compare_logic/summary.json` 和 `movvp/summary.json` 保存实测事件及发射/完成时间。
MOVVP -> VSEL观察到13-cycle间隔，尚未做间隔扫描，不作为最终forwarding值。
VSEL的CCE操作数为FP32，RV日志打印B32，两种命名口径需区分。

验证：Python 266项（265通过、1项可选依赖跳过）；Native 8/8。

## 运行与后续

```bash
python3 cce_code/predicate_select_test/compute_sweep.py --out /tmp/predicate-compute-new
python3 cce_code/predicate_select_test/run.py --probe predicate_compute --operation vcmp_ne
```

脚本为每条指令保留生成CCE、golden、输出、编译命令、core0 IDU/ISU/EXU、
instr_log/instr_popped_log，以及结构化summary.json。数值失败和编译失败非零退出。
MOVVP先使用u32全零/全一、part=0验证，后续需要混合bit pattern及part/位宽矩阵。
下一步分别增加独立指令self-II、依赖链forwarding、端口样本与FP16/整数forms。
所有未实测项保留“未覆盖”状态，不能通过默认值算作benchmark通过。
