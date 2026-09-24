# A5 PSET mask forwarding 隔离实验

## 结论与范围

2026-09-24，A5 dav-c310-vec / Ascend950PR_9599。
7组探针的64元素输出均通过 FP32 逐位精度校验。
在排除向量源等待的样本中，观测到：

- PSET_B32 -> VDUP(scalar)：发射间隔2 cycles。
- PSET_B32 -> VADD.fp32：发射间隔2 cycles，PAT_ALL 和 PAT_VL32 均覆盖。
- PSET_B32 发射到 retire：6 cycles。
- mask 可在 producer retire 前使用，不应等待全部 latency。

这里 forwarding 使用 EXQ issue / instr_popped_log 的 producer-to-consumer 间隔。
SHQ wakeup-ready 则发生在 producer issue 后1 cycle，之后还有1 cycle EXQ转移。
将来写入 VfSim 参数时必须检查 forwarding 参数代表哪一级就绪，避免重复加这1 cycle。
此次没有修改 Core 或 timing 参数；尚未测 self-II、B8/B16 和其他消费者。

## 探针设计

无 load 探针：`cce_code/pset_vdup_single.cce`，VF 内只有 PSET、VDUP、VSTS。
VF 外保留与原探针一致的 GM->UB 和事件等待，避免入口条件变化影响 VF 总时间。
初次未保留该启动序列时 VF 耗时464 cycles，但 PSET->VDUP 仍为2；
该运行不用于启动开销校准。统一外层条件后的正式运行是49 cycles。

VADD 扫描：两条 VLDS 后插入 G 组 VADDS+VSTS，再生成新 mask，执行目标 VADD。
填充指令不依赖目标 PSET，结果写入独立 scratch UB 防止删除。
前后 PSET 使用不同 pattern，防止公共子表达式合并。
目标 VADD 的结果用全有效 mask 写出，golden 同时检查非激活 lane 的 zeroing。
生成器：`cce_code/predicate_select_test/forwarding_source.py`。

## 实测结果

时间以 VF PUSHQ 为0，统一使用 popped / EXQ issue 口径。

| 探针 | G | 目标 mask | PSET issue | consumer issue | 间隔 | VF cycles |
|---|---:|---|---:|---:|---:|---:|
| VDUP，无VLDS | 0 | ALL | 22 | 24 | 2 | 49 |
| VADD | 0 | VL32 | 22 | 27 | 5 | 53 |
| VADD | 4 | VL32 | 24 | 29 | 5 | 57 |
| VADD | 8 | VL32 | 25 | 31 | 6 | 61 |
| VADD | 12 | VL32 | 33 | 35 | 2 | 65 |
| VADD | 16 | VL32 | 55 | 57 | 2 | 83 |
| VADD | 12 | ALL | 33 | 35 | 2 | 65 |

G=0/4/8仍存在 vector 未就绪或排队竞争，不能拿这些间隔标定 mask forwarding。
G=12/16的两条 load 都在相对cycle30完成，目标PSET分别在33/55发射，
目标VADD记录 `SRC_NOT_READY_PREG`，随后 wakeup-ready 并立即进入EXQ发射，
因此可隔离出 mask 路径的2-cycle有效间隔。

例如 G=12、PAT_ALL 的绝对时间线：

```text
VLDS全部完成       PSET issue      VADD SHQ ready      VADD EXQ issue     PSET retire
1655               1658            1659                1660               1664
```

编译后的PC仍将目标PSET置于填充指令后；目标VADD读取其Pd对应的Pg。
VDUP探针为跨EXU（PSET EXU1 -> VDUPS EXU0），G=12 VADD为同EXU1，均观察到2。
这不等于覆盖所有opcode、mask位宽或所有端口组合。

## 复现与归档

```bash
python3 cce_code/predicate_select_test/run.py --probe pset_vdup_single
python3 cce_code/predicate_select_test/run.py --probe pset_vadd_gap --gap 12
python3 cce_code/predicate_select_test/run.py --probe pset_vadd_gap --gap 12 --target-pattern PAT_ALL
```

扫描 gap=0/4/8/12/16。脚本输出独立运行目录，包含生成CCE、编译命令、golden和日志。
`analyze_forwarding.py <运行目录...> --out <新归档目录>` 自动提取消费者的Pg、
匹配对应PSET的Pd，并归档证据和summary.json。
本轮归档：`results/pset_forwarding_probe/camodel_20260924/`。
这些results由git忽略；本文件与可执行探针脚本保留复现方式。
