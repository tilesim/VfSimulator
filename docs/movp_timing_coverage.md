# MOVP 指令覆盖与 A5 时序参数

MOVP_B16/B32 从 VF 外设置的 MASK0/MASK1 特殊寄存器读取 mask，转换成 VF 内的 PredicateRegister。它们不是 PMOV/PAND，也不是 MOVVP。

| 指令 | form | latency | EXU | self-II | 到 VDUP/VSEL 的 forwarding | 到 PSTS.b8 的 forwarding |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| MOVP_B16 | b16 | 6 | ALU，EXU01 | 1 | fp16：2 | 4 |
| MOVP_B32 | b32 | 6 | ALU，EXU01 | 1 | fp32：2 | 4 |

## 输入与依赖

```cpp
vector_bool p = movp_b16();
p = movp_b32();
```

canonical 输入使用 `MOVP_B16/b16` 或 `MOVP_B32/b32`，只声明一个 bool 类型的 PredicateRegister 输出，没有普通 vector 输入/输出。
消费者通过输出的 SSA value_id 建立 predicate 依赖，复用现有重命名和释放逻辑，不消耗 vector 物理寄存器。
mask 的 VF 外设置耗时不在本次建模范围内。未测 consumer 及交叉 II 组合保留原来的 fallback 和 warning。

TileSim 调用方应将输出显式声明为 PredicateRegister；仅提供 opcode 或将输出声明为普通 vector register，不能建立正确的谓词依赖。

本接入分支同步根目录及 `vfsimulator/` Python 包副本，并通过 VfSimProgram 实际入口验证。Native 的 canonical/predicate 接入在 master 验证；本次不将整套 Native 后端重构带入本分支。
本分支尚无 PSTS 指令语义，MOVP -> PSTS 的配置项仅保留已测参数，不代表已新增 PSTS 支持。

## 测量依据

2026-10-16/18 使用 CANN 9.0.0-beta.1、Ascend950PR_9599、dav-c310-vec 的 CAModel，O2、vec-misched=0。不是 CANN 9.1.0 真机增量校准。

14 个初始探针检查全零/全一/稀疏 mask、B16 高低半区、B32 低半区及 64B 尾部保护区；2752 个输出值/字节校验通过。
latency 使用 instr_log.done 减 instr_popped.start；forwarding 使用真实 RAW 启动间隔。隔离 vector 输入就绪时间后，MOVP -> VSEL 为 2，而不是初始混合探针观察到的 4。

重复调用 MOVP 会被编译器合并，不能用源码次数测 II。后续编译不同 PAT_VL 的 PSET 控制版，保留 4/6 个独立 predicate 输出；仅将目标机器指令替换成相同 Pd 的 MOVP。
编码以编译器真实输出的 Pd[1]/Pd[2] 为参考，ELF `.text` 只改变目标指令，长度和其余指令保持不变。
控制版和替换版均做独立数值校验，并核对 PC 相对偏移、opcode、操作数和无 spill，测量对象是替换后的 ELF，不是被 CSE 合并后的 CCE。

| 位宽 | MOVP 数量 | EXU0 相邻启动间隔 | EXU1 相邻启动间隔 |
| --- | ---: | --- | --- |
| B16 | 4 | 1 | 2 |
| B16 | 6 | 1、2 | 2、1 |
| B32 | 4 | 1 | 2 |
| B32 | 6 | 1、2 | 2、1 |

同一 EXU 的最小间隔均为 1，且观察到不同 EXU 同周期各启动一条 MOVP。间隔 2 包含探针中其他指令的仲裁影响，不是最小 II。
四组控制/目标共 8 个有效版本、3840 个输出元素及各自保护区校验通过。未修改调度策略、cap=7 或寄存器释放规则。

原始本地实验分别归档在 `results/a5_movp_parameters_20261016` 和 `results/a5_movp_ii_parameters_20261018`；结果目录默认不随 Git 跟踪。本次同步仅包含正式支持、配置、生成目录和聚焦回归测试。

## 分支验证

MOVP、program API、谓词 program 和 Python 包测试合计 37 项通过，包副本同步检查通过；两种位宽均验证实际 VfSimProgram 入口的 canonical 转换、谓词依赖和预测周期。

全量 pytest：355 项通过、2 项失败、7 项跳过。两项失败是原分支已存在的测试/文件不一致：`test_instruction_catalog.py` 要读取 C++ 的 InstructionCatalogData.inc 和 UarchOverrideSchemaData.inc，但原始提交没有这两个文件。本次没有通过跳过测试或恢复旧 Native 文件来掩盖它们。
