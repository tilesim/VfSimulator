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
首批探针阶段只落PSET；当前落参状态见下节。
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

## 当前模型覆盖（2026-09-24）

- VCMP/VCMPS 的 EQ/NE/GT/GE/LT/LE 已登记 FP32 latency=6、ALU/EXU01。
  VCMP_EQ 原有 FP16 参数及显式 forwarding/II 保留，不覆盖历史已配置的 pair。
- PAND/POR 接入共享 Catalog 和生成的 Native 表，固定 form=b8（探针 RV 日志口径），
  latency=7、ALU/EXU01。EXU01 仍采用本阶段端口可用性近似，不宣称已测全双端口。
- CCE 调用为 `pand/por(dst, lhs, rhs, mask)`；目的为 PredicateRegister，三个输入
  都是 OperandRole.PREDICATE，不忽略控制 mask，不分配 vector 目的寄存器。
  循环携带、SSA、重命名和 start+4 释放复用统一谓词路径，不增加 opcode 特判。
- 未实测 forwarding 使用已有 latency-3 兜底并 warning，因此 PAND/POR 缺省为4，
  新增比较指令缺省为3；显式 pair 优先。II 仍沿用现有缺省冲突近似并 warning。
- 新增比较的 FP16/b16 等形式尚未补测，仍是兼容/default timing fallback，不能视作实测覆盖。
- MOVVP 现已接入 `movvp(predicate, vector_u32, part)`：Catalog form 使用源 dtype
  `uint32`，对应硬件 B32；part 为0..31的整数字面量，选择8 B数据块。
  latency=16（已测part=0全零/全一），其他part沿用该值，未逐项实测。
  单元暂定ALU/EXU01；forwarding缺失时沿用latency-3=13，II仍用默认近似并warning。
  读取一个vector preg并产生一个predicate preg，复用现有SSA/循环/重命名/释放路径。
  B16已补测并支持（见下节）；其他源类型仍明确拒绝。MODE_MERGING 仍不支持。
  原探针的 `vdup(vector_u32, 0xffffffffu, ...)` 也已补齐语义形式和十六进制常量识别；
  VDUP.uint32 尚未独立落参，仍使用已有 timing fallback/warning，未视作新校准结果。

测试 `tests/test_predicate_logic.py` 覆盖12种比较、PAND/POR 的三源谓词依赖和循环回边、
非法操作数、forwarding 兜底以及 Python/Native 逐指令发射周期与总周期一致性。

## MOVVP B16 补测（2026-09-25）

测试为 `vector_u16 bits`，使用 PSET_B16 + VDUP 生成全一/全零位模式，执行
`movvp(greater, bits, 0)` 后用 FP32 VSEL 消费结果，与独立 golden 按字节比较。
RV 日志确认实际发射 `RV_MOVVP Dtype: B16`，不是B32或编译期常量折叠。

| 位模式 | MOVVP issue | MOVVP done | latency | VSEL issue | golden | VF cycles |
|---|---:|---:|---:|---:|---|---:|
| 全一 | 1652 | 1668 | 16 | 1665 | 通过 | 65 |
| 全零 | 1652 | 1668 | 16 | 1665 | 通过 | 65 |

`configs/isa.json` 登记 MOVVP.uint16 latency=16，沿用ALU/EXU01近似。
缺失forwarding仍按16-3=13兜底并warning；观测到13-cycle间隔，但未做隔离扫描，
不宣称完整forwarding矩阵已校准。self-II仍缺省近似。
Catalog中uint16对应B16，part允许0..15；uint32对应B32，part允许0..31。
form相关参数约束由通用 `allowed_values_by_form` 描述，不在调度器添加MOVVP特判。
Native生成表同步携带该元数据；part是CCE立即数配置，不产生动态寄存器依赖。
前端补齐u16到uint16的dtype归一化和VDUP.uint16语义，VDUP的该形式仍用timing fallback。
part=15已有Python/Native模型测试，但硬件仅测part=0全零/全一，未覆盖混合pattern。

复现命令（输出目录必须不存在）：

```bash
python3 cce_code/predicate_select_test/compute_sweep.py --out /tmp/vfsim-movvp-b16-probe --operations movvp_b16_ones movvp_b16_zeros
```

归档：`results/predicate_compute_benchmark_20260924/movvp_b16/`，包括summary.json、
两份CCE、编译和运行日志、golden及输出、core0 instr/IDU/ISU/EXU日志。
results不随Git分发；上述命令可重新生成。

## 外部 Canonical form 兼容

MOVVP接受b16/b32及uint16/uint32，分别使用相同16-cycle latency和单元参数。
PSTU同样支持两套form，span、隐式指针推进及到VSTAS的forwarding同步覆盖。
不把其他指令的b16/b32全局解释成无符号整数。直接JSON的合同fixture和测试位于
`tests/fixtures/canonical_vf_info/v2_external_predicate_forms.json`、
`tests/test_external_predicate_forms.py`。这是按报告构造的测试，不是真实Adapter导出。
