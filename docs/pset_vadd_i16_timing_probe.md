# A5 PSET + VADD I16 启动时序穿刺

## 运行方式与结果

2026-09-24，`vfinfo-core-api-unification` 工作区（含未提交谓词建模）。
源码：`cce_code/pset_vadd_i16.cce`。循环外一次 `pset_b32(PAT_ALL)`，
循环内两条 VLDS、一条 VADD、一条 VSTS，16 次，不展开。
每轮处理独立的 64 个 FP32 元素，无 Membar。

```bash
python3 cce_code/predicate_select_test/run.py --probe pset_vadd_i16
python3 main.py --cce cce_code/pset_vadd_i16.cce --out_dir /tmp/vfsim-pset-vadd-i16
```

- CAModel：A5 dav-c310-vec，VF 72 cycles；1024 个输出逐位匹配 golden，最大绝对误差 0。
- 当前 VfSim：75 cycles，PSET 使用缺失 ISA 的 latency=9 和 forwarding=6 默认值，尚未校准。
- 原始日志及数值验证：`results/pset_vadd_i16/camodel_20260924/`。
- VfSim 日志：`results/pset_vadd_i16/vfsim_20260924/`。
- 编译、链接、运行命令均在 CAModel `validation.json` 中；脚本可重新生成日志。

## CAModel 时间线

以 `instr_popped_log` 的 VF PUSHQ 时刻 1761 为零点。
注意 popped 是出队/发射口径，EXU PERF 是执行级口径，不能混用。

| 事件 | 绝对 cycle | 相对 VF cycle | 证据 |
|---|---:|---:|---|
| VF PUSHQ | 1761 | 0 | instr_popped_log |
| VLOOP；PSET IDU dispatch | 1780 | 19 | popped；IDU |
| PSET 进入并离开 SHQ | 1782 | 21 | ISU |
| PSET 进入并离开 EXQ1 / popped | 1783 | 22 | ISU；popped |
| PSET EXU1 执行起点 | 1784 | 23 | EXU PERF |
| 首两条 VLDS popped | 1785 | 24 | popped |
| PSET retire | 1789 | 28 | EXU PERF；instr_log |
| 首条 VADD popped | 1792 | 31 | popped |
| 首条 VADD EXU 执行起点 | 1793 | 32 | EXU PERF |
| 首条 VADD retire | 1799 | 38 | instr_log |
| 最后一条 VADD retire | 1814 | 53 | instr_log |
| 最后一条 VSTS 完成 | 1821 | 60 | instr_log |
| VF 完成 | 1833 | 72 | instr_log，vf_execute_time=72 |

实际动态计算指令为 1 条 RV_PSET、16 条 RV_VADD；搬运为 32 条 RV_VLDS、16 条 RV_VSTS。
编译器将访存转换为带 post-update 的形式；这不是纯粹的 PSET timing 测试。

## 如何解释与改造模型

1. PSET 确实经过 SHQ、EXQ1、EXU1；不是只由头开销代表的零成本指令。
   本例只能证明 EXU1 样本，不能单独证明全部合法端口。
2. PSET 从 popped 到 retire 为 6 cycles，从 EXU PERF 起点到 retire 为 5 cycles。
   当前 VADD 配置 latency=7，对应本例 popped 1792 到 retire 1799 的差值，
   而不是 EXU PERF 的 6。因此按现有参数口径，PSET_B32 的 latency 候选应为 6，
   不能直接把 PERF 差值 5 写入配置。仍需独立探针验证后正式落参。
3. 首条 VADD 比 PSET popped 晚 9 cycles，但同时等待 VLDS，不能把 9 当成最小 forwarding。
   本例没有两条 PSET，也无法测 self-II。后续应使用 PSET -> VDUP 等无 load 数据依赖探针，
   再分别测 masked compute/store 和不同位宽。
4. 当前配置的 19 是 `initial_top_block_vloop_start_cycle`；
   `vf_startup_cost=23`，`idu_dispatch_start_advance=2`，因此 IDU 总入口门槛为 21。
   不能把 19 解释成 PSET latency，更不能直接做 `19 - PSET latency`。
5. 后续改造应明确区分 VF 入口到 IDU 可分发、VLOOP/body 开放、PSET 正常排队执行、
   mask RAW forwarding，以及 VF drain。PSET 与 load/循环控制允许重叠，
   不应在启动门槛后再强制所有指令统一等待一个 PSET 延迟。

本次当前模型 PSET start/done=24/33，首条 VADD=30/37，最后 VSTS done=63。
与硬件相比，PSET 完成偏晚，但首条 VADD 反而偏早，说明启动门槛、默认 forwarding、
访存与排队时序必须分别核对；不能仅凭总时间差 3 cycles 反推 PSET 参数。
本次仅增加探针、复用 host 的可配置输出长度和记录证据，没有修改 ISA/startup 配置。

## 无循环对照实验

源码：`cce_code/pset_vadd_single.cce`，仅 PSET、两条 VLDS、一条 VADD、一条 VSTS。

```bash
python3 cce_code/predicate_select_test/run.py --probe pset_vadd_single
python3 main.py --cce cce_code/pset_vadd_single.cce --out_dir /tmp/vfsim-pset-vadd-single
```

CAModel 64 个 FP32 输出逐位校验通过，最大绝对误差 0，VF 总时间 53 cycles。
当前未校准 PSET 参数的 VfSim 为 56 cycles。日志归档在
`results/pset_vadd_single/{camodel_20260924,vfsim_20260924}/`。
编译后无 RV_VLOOPv2，load/store 为 RV_VLDI/RV_VSTI。

| CAModel 事件（相对 VF 开始） | 无循环 | 16轮 |
|---|---:|---:|
| PSET IDU dispatch | 19 | 19 |
| PSET SHQ recv/issue | 21 | 21 |
| PSET EXQ issue / popped | 22 | 22 |
| PSET EXU PERF 开始 | 23 | 23 |
| PSET retire | 28 | 28 |
| 首两条 load popped | 21 | 24 |
| 首条 VADD popped | 27 | 31 |
| 首条 VADD retire | 34 | 38 |

无循环 VF 绝对起点为 1624：PSET popped=1646、retire=1652；
两条 load popped=1645；VADD popped=1651、retire=1658。
ISU 中 VADD 在 1645 进入 SHQ 时记录 `SRC_NOT_READY_VREG`，
1650 wakeup-ready 并离开 SHQ，1651 从 EXQ1 发射。

结论：去掉循环并未改变 PSET 自身的启动时间，却让 load 和 VADD 更早推进。
PSET->VADD 实际发射间隔缩短至 5 cycles，且 VADD 在 PSET retire 前 1 cycle 发射，
说明不必等 PSET 退休。但 VADD 同时读两个 load 结果，日志明确出现 vector 数据未就绪，
因此 5 仍只能视为本例观测间隔，不能据此断言是最小 mask forwarding。
此结果不支持从 19-cycle 入口延迟中扣除 PSET latency。
当前 VfSim 的 PSET=24/33、VADD=30/37、VSTS=35/44（start/done）；参数未修改。
