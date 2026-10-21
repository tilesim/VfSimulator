# FP32 VEXP / VDIV 发射间隔与三连实验

目标环境为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、
`dav-c310-vec`。数据类型为 FP32，使用完整 mask，时间单位为 cycle。

## 独立流与转换间隔

28 个 VF 案例、528 条目标指令全部通过数值、保护区和实际指令数校验。
每组使用两份不同输入，动态寄存器分析确认目标计算指令之间没有 RAW。

| 测量对象 | 同端口间隔 | 达到该间隔的有效样本数 |
| --- | ---: | ---: |
| VEXP 连续独立流 | 4 | 76 |
| VDIV 连续独立流 | 4 | 76 |
| VEXP → VDIV | 6 | 14 |
| VDIV → VEXP | 5 | 14 |

每项均在 EXU0、EXU1 和两份输入中重现。转换间隔的参数样本要求 consumer 在
前一条发射后的第一个槽位已经源可用、已进入 EXQ 且提前分发，并排除 consumer
自身更早同 opcode 的 self-II 等待。源可用时间采用 ISU 的 `src_rdy`，包含 forwarding。

## 同端口实际三连

重复源模式 `VEXP;VDIV;VEXP` 和 `VDIV;VEXP;VDIV` 时，硬件会分流、乱序发射，
本批普通模式在每个端口形成同 opcode 的连续段。源代码顺序单独保存于 `cases.json`；
实际发射顺序按动态 ID、端口和周期还原。

受控穿插采用每组两条独立指令，分别向两个 EXU 提供工作：

```text
VEXP,VEXP → VDIV,VDIV → VEXP,VEXP
VDIV,VDIV → VEXP,VEXP → VDIV,VDIV
```

末组使用四次 PNOT 得到的延迟谓词，其值保持完整 mask。每个端口均观察到以下
三条目标指令的顺序；表中以第一条目标指令的发射时刻为零：

| 同端口目标指令序列 | 发射时刻 | 两段间隔 | 两端同类指令间隔 | 样本数 |
| --- | --- | --- | ---: | ---: |
| VEXP → VDIV → VEXP | 0、6、11 | 6、5 | 11 | 4 |
| VDIV → VEXP → VDIV | 0、5、11 | 5、6 | 11 | 4 |

每个方向的四个样本来自两个 EXU 和两份输入。三连按目标指令计数，期间存在支持
谓词的 PNOT；完整计算流保存在机器证据中。末条目标指令在相对时刻 8～10 才源可用，
因此这些三连用于确认受控穿插的实际顺序及等待，不能独立标定三条全部提前就绪时的
最小三连间隔。转换间隔 6、5 由上一节的独立有效样本支持。

具体事件位于 `vfsim-exp-div-focus-staged4-20261020/batch_000`：

| 模式 | replica | 端口 | 动态 ID | 发射时刻 | 源可用时刻 |
| --- | ---: | ---: | --- | --- | --- |
| VEXP → VDIV → VEXP | 0 | 0 | 156、158、160 | 3828、3834、3839 | 3828、3829、3837 |
| VDIV → VEXP → VDIV | 0 | 0 | 251、253、255 | 3912、3917、3923 | 3912、3913、3921 |

例如第二行的末条 VDIV 在 3921 源可用，其上一条同端口 VDIV 发射于 3912，
self-II=4 已满足；上一条 VEXP 发射于 3917，通用 4-cycle 限制也已满足。
ISU 在 3921 记录 `VALU_RESC_CFLT`，实际发射于 3923。该周期同端口没有其他
计算指令发射，支持 VEXP/VDIV 之间还有更具体的资源耦合限制。

## 对时序模型的约束

- VEXP 和 VDIV 各自的 self-II 为 4，穿插其他指令后仍需保留相应约束。
- 对本批 FP32、完整 mask 和已测上下文，还需解释 VEXP→VDIV 的 6-cycle 间隔
  与 VDIV→VEXP 的 5-cycle 间隔。仅分别检查各 opcode 的 self-II，或统一采用
  `II(SFU,SFU)=4`，均不能完整解释这些事件。
- 三连中观测到的 11 是该混合序列的发射跨度，VEXP、VDIV 的 self-II 保持为 4。
- 上述观测不能唯一确定硬件内部流水线，也不能推广到其他 dtype 或全部 SFU。

本次交付探针和测量证据，模型调度与参数配置保持现有值。

## 工具、证据与复现

使用 skill 的 `scripts/fu_contention.py` 生成、运行并校验探针，
`scripts/analyze_fu_contention.py` 关联 popped、EXU、ISU 和 IDU 事件。
`--tail-gate-chain 4` 保持前段谓词可用时间，仅延迟最后两条目标指令。

基础组包含 self、重复三连和单次转换，共 12 个案例，每个案例 18 条目标指令。
扩展组使用相同名称、每个案例 24 条目标指令和四次共同 PNOT，共 12 个案例；
源序列按指定模式循环展开，具体顺序由 `cases.json` 记录。
受控组包含两个 paired 模式、两份输入，共四个案例，每个案例六条目标指令。

```bash
python3 skills/vfsim-instruction-benchmark/scripts/fu_contention.py --out /tmp/exp-div-basic --names vexp_self vdiv_self vexp_vdiv_vexp vdiv_vexp_vdiv vexp_then_vdiv vdiv_then_vexp --count 18 --replicas 2 --batch-size 6
python3 skills/vfsim-instruction-benchmark/scripts/fu_contention.py --out /tmp/exp-div-ready --names vexp_self vdiv_self vexp_vdiv_vexp vdiv_vexp_vdiv vexp_then_vdiv vdiv_then_vexp --count 24 --replicas 2 --gate-chain 4 --batch-size 6
python3 skills/vfsim-instruction-benchmark/scripts/fu_contention.py --out /tmp/exp-div-staged --names vexp_vdiv_vexp_paired vdiv_vexp_vdiv_paired --count 6 --replicas 2 --tail-gate-chain 4 --batch-size 4
python3 skills/vfsim-instruction-benchmark/scripts/analyze_fu_contention.py --runs /tmp/exp-div-basic /tmp/exp-div-ready /tmp/exp-div-staged --out /tmp/exp-div-analysis.json
```

为 `--out` 指定未使用的目录。[机器证据](evidence/a5-fp32-exp-div-triples.json)
保存逐条目标事件、源就绪、阻塞原因、动态 ID、日志行号和原始文件 SHA256。
原始源码、输入、golden、输出、编译运行命令及活动向量核日志保存于
`results/a5_fp32_exp_div_triples_20261020/`，由 `manifest.json` 索引。
