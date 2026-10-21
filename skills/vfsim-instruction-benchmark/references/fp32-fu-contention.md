# FP32 VCMAX、ALU 与数学 SFU 竞争实验

VEXP 与 VDIV 的同端口三连、转换间隔和受控穿插见
[FP32 VEXP / VDIV 实验](fp32-exp-div-triples.md)。

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`、
`__NPU_ARCH__=3101`。本报告仅讨论 FP32，数值单位为 cycle。

## 结果

52 个独立 VF 案例、936 条目标计算指令通过数值校验。每个 VF 包含 18 条
没有目标间 RAW 的计算指令，两份 replica 使用不同输入。
VCMAX 的最大值和索引按原始字节精确校验，仅存储两个有定义的结果 lane。
VEXP/VDIV 使用有限正输入，按逐运算 FP32 golden 校验全部输出。

| 指令 | 独立流 self-II | 实际端口 |
| --- | ---: | --- |
| VCMAX | 1 | EXU0 |
| VADD | 1 | EXU0、EXU1 |
| VMUL | 1 | EXU0、EXU1 |
| VEXP | 4 | EXU0、EXU1 |
| VDIV | 4 | EXU0、EXU1 |

下表按实际同端口相邻发射取最小有效间隔，排除源未就绪、分发过晚，以及
consumer 更早同 opcode 的 self-II 等待。每项均在两份 replica 中重现。
双端口指令对覆盖两个端口；包含 VCMAX 的指令对只统计 EXU0。

| 同端口指令对 | 最小间隔 | 达到最小值的样本数 |
| --- | ---: | ---: |
| VCMAX→VADD | 3 | 6 |
| VADD→VCMAX | 1 | 2 |
| VCMAX→VEXP | 1 | 24 |
| VEXP→VCMAX | 1 | 25 |
| VADD→VMUL | 1 | 20 |
| VMUL→VADD | 2 | 20 |
| VEXP→VDIV | 6 | 6 |
| VDIV→VEXP | 5 | 6 |
| VADD→VEXP | 1 | 12 |
| VEXP→VADD | 1 | 16 |

这些值适用于本批输入、完整 mask 和已测指令对。VCMAX→VADD 的配置现值为 4，
实验中可达到 3；该历史配置不能作为最小硬件间隔的依据。
其他归约 opcode、dtype、部分 mask 和不同数学指令的转换仍需各自验证。

## 四种源序列的实际发射

源程序重复 `VCMAX;VADD;VCMAX`、`VADD;VCMAX;VADD`、
`VEXP;VCMAX;VEXP`、`VCMAX;VEXP;VCMAX`，分别采集实际发射流。

VCMAX 只能使用 EXU0，VADD/VEXP 可使用两个端口。源程序的相邻指令可以分流到不同
端口，并发生硬件乱序发射。因此，源序列和同端口的实际三连序列分别保存。

| 源模式 | 观测 |
| --- | --- |
| VCMAX;VADD;VCMAX | EXU0 的 VCMAX 可以继续以 1 间隔发射，部分 VADD 被资源冲突阻塞；实际流发生重排。另用单条 VCMAX 后接独立 VADD 流确认转换间隔 3。 |
| VADD;VCMAX;VADD | VADD 被分流至两个端口，实际 EXU0 序列发生重排。独立 VADD 流后接单条 VCMAX 的对照确认反向转换间隔 1。 |
| VEXP;VCMAX;VEXP | 实际 EXU0 三连示例为 `VEXP@6666 → VCMAX@6667 → VEXP@6670`。VCMAX 插入后，两条 VEXP 仍相隔 4。 |
| VCMAX;VEXP;VCMAX | 实际 EXU0 三连示例为 `VCMAX@6765 → VEXP@6766 → VCMAX@6767`。两次跨指令转换均为 1。 |

机器证据保留每个源模式、实际端口流、完整动态 ID、三连样本及其源就绪条件。
单条穿插对照另测 VCMAX→VADD、VADD→VCMAX、VCMAX→VEXP、VEXP→VCMAX、
VEXP→VDIV、VDIV→VEXP，减少其他在途目标指令对转换边界的干扰。

## 资源归属的判断

VCMAX 的 self-II=1、EXU0 限制和与 VEXP 的双向 1-cycle 转换，支持将它与数学
SFU 分开维护时序历史。仅按当前 `EXU=SFU` 标签统一使用 4-cycle 间隔，会过度约束
VCMAX 与 VEXP 的交错。

VCMAX→VADD=3 和反向为 1，表明它与 ALU 路径仍有资源耦合。
在隔离转换对照中，VCMAX 于 6609 发射；同端口 VADD 于 6610 已进入 EXQ 且源可用，
该周期被 `VALU_RESC_CFLT` 阻塞，实际于 6612 发射。

时序模型适合为归约设置单独的 `REDUCE` 类别；当前证据不能唯一还原硬件执行单元
拓扑，也不能证明它具有完全独立的物理流水线。ALU/SFU/REDUCE 应表示实际时序资源
的分组，跨组的共享资源仍需处理。

## self-II 与默认间隔

**self-II 加每端口每周期一条，不能完整解释本批结果。**

- VADD、VMUL 的 self-II 均为 1，但 VMUL→VADD 需要 2。
- VEXP、VDIV 的 self-II 均为 4，两个转换方向分别观测到 6、5。
- VCMAX→VADD 需要 3，即使 consumer 没有更早同 opcode 的 self-II 约束。
- 插入 VCMAX 后，VEXP 自身的 4-cycle 约束仍持续生效。

ISU 中存在源已可用、EXQ 已接收、该端口当周期没有发射计算指令且 self-II 已满足，
仍被 `DST_PORT_LIMIT` 或 `VALU_RESC_CFLT` 阻塞的样本。机器证据记录了 364 个
满足这些筛选条件的阻塞周期；它们来自同一批运行，不能当作 364 个独立实验。

例如某条 VADD 在 6472 具备上述条件，但被 `DST_PORT_LIMIT` 阻塞。
若按 latency=7 发射，它将在 6479 完成；更早 VCMAX 已占用相同端口的 6479 完成时刻。
这一观测与共享写回资源冲突一致。其他 `VALU_RESC_CFLT` 样本不能单靠完成时刻碰撞解释。

建议的 II 建模边界是：

1. 每个 EXU 每 cycle 最多接收一条计算指令。
2. 按时序资源类别保存发射历史，数学 SFU 历史不被 ALU 或归约覆盖。
3. 保留实测 opcode/form 转换间隔；self-II 是其中一部分。
4. 共享资源单独约束，条件成熟时以写回时刻和内部资源预留替代部分固定 pair 近似。
5. `II(ALU,ALU)=1`、`II(SFU,SFU)=4` 可作为相应范围的初始回退，实测例外优先。

本轮交付测量、分析工具和建模建议。调度逻辑、FU 分类与参数配置保持现有值，
供后续确定资源模型后统一调整。

## 隔离方法与复现

所有输入先加载并原样回写，完成访存屏障后使用 PLDS 的共同谓词开放目标流。
第二组增加四次 PNOT，维持 FP32 全 mask 并延长目标指令的等待窗口。
实际 RV 指令数量和四次 PNOT 均从日志核对。

分析同时读取 popped、完成、EXU、IDU 和 ISU 事件。ISU 的 `src_rdy` 包含 forwarding
可用性；多周期候选要求 consumer 在前一条发射后的第一个合法槽位已可用、已进入 EXQ，
并且提前分发。单周期观测本身达到每端口的最小间隔。

```bash
python3 skills/vfsim-instruction-benchmark/scripts/fu_contention.py --out /tmp/fu-basic --names vcmax_self vadd_self vexp_self vmul_self vcmax_vadd_vcmax vadd_vcmax_vadd vexp_vcmax_vexp vcmax_vexp_vcmax vadd_vexp_vadd vexp_vadd_vexp vadd_vmul_vadd vmul_vadd_vmul vexp_vdiv_vexp vdiv_vexp_vdiv
python3 skills/vfsim-instruction-benchmark/scripts/fu_contention.py --out /tmp/fu-ready --gate-chain 4 --names vcmax_self vcmax_vadd_vcmax vadd_vcmax_vadd vexp_vcmax_vexp vcmax_vexp_vcmax vcmax_then_vadd vadd_then_vcmax vcmax_then_vexp vexp_then_vcmax vdiv_self vexp_then_vdiv vdiv_then_vexp
python3 skills/vfsim-instruction-benchmark/scripts/analyze_fu_contention.py --runs /tmp/fu-basic /tmp/fu-ready --out /tmp/fu-analysis.json
```

[精简机器证据](evidence/a5-fp32-fu-contention.json) 保存逐条事件及 SHA256。
原始源码、输入、golden、输出、命令和日志保存于
`results/a5_fp32_fu_contention_20261020/`，由 `manifest.json` 索引。
该目录还保留 PNOT 参数个数不匹配的编译诊断；这些样本没有输出，也没有提取时序参数。

验证通过 52 个数值案例、5 项新增分析测试和 416 项完整 Python 测试，
完整测试包含配置 `VFSIM_NATIVE_RUNNER` 的双实现对照。
