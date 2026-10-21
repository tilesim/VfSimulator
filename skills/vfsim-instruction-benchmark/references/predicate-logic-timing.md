# A5 谓词逻辑与复制

环境为 CANN 9.0.0-beta.1、Ascend950PR_9599、`dav-c310-vec`（`__NPU_ARCH__=3101`），
O2、fusion=false、misched=0。测量日期为 2026-10-19。

## 输入合同与时序

| CCE API | Canonical 指令 | latency | 端口 | self forwarding | 已确认 self-II |
| --- | --- | ---: | --- | ---: | ---: |
| `pnot(dst,src,mask)` | `PNOT.b8` | 6 | EXU01 | 2 | 1 |
| `pxor(dst,lhs,rhs,mask)` | `PXOR.b8` | 7 | EXU01 | 3 | 待测 |
| `psel(dst,lhs,rhs,mask)` | `PSEL.b8` | 7 | EXU01 | 3 | 待测 |
| `pmov(dst,src)` | `PAND.b8(dst,src,src,src)` | 7 | EXU01 | 3 | 待测 |
| `pmov(dst,src,mask)` | `PAND.b8(dst,src,src,mask)` | 7 | EXU01 | 3 | 待测 |

PNOT、PXOR、PSEL 和 PMOV 使用 bool 类型的 PredicateRegister。PMOV 在 Catalog 中声明为
虚拟入口，CCE adapter 绑定实际参数后生成 PAND；Canonical 输入使用对应的 PAND 指令和
三个 predicate 输入。Python/C++ 共享签名、specialization 和参数文件。

参数写入 `isa.json` 的三个新增 form、`forwarding.json` 的四个 self pair，以及
`InitiationInterval.json` 的 `PNOT.b8 → PNOT.b8 = 1`。PAND 的 latency 沿用其精确配置项。

## 数值验证

`run.py --probe predicate_compute --operation OP` 分别覆盖 PNOT、PXOR、PSEL 和 PMOV 两种
写法。FP32 的 greater／less／equal 输入经独立 golden 计算，64 个输出逐 bit 比较通过。
PMOV 两个探针的实际动态 opcode 均为 RV_PAND。

`memory_probe.py --kind predicate_logic_matrix --iterations 1` 生成四个独立 VF，分别包含
PNOT、PXOR、PMOV、PSEL。4096-byte 输入包括不同的 256-bit predicate bitmap 和部分 mask；
2560-byte 输出保存独立流结果、按不同源位置串接的中间结果及保护区。
全部字节匹配独立整数位运算 golden。增加 VV_ALL 的控制实验同样通过。

每个 VF 先执行四条操作不同 predicate 的独立指令，再执行数据源／第二数据源／mask 的
RAW 链，链中每一步均写入单独的 UB 槽。PNOT、PAND 各保留 12 条动态目标指令；PXOR、
PSEL 各保留 16 条。未观察到额外 predicate spill。

## 提取与适用范围

分析器按同一运行、core0.veccore0、VF PC 范围及动态 ID/opcode 关联 popped、done 和
EXU PERF。PERF 起点为 popped+1；latency 使用 done−popped。
predicate STORE 日志中的 `Pd` 为读取源，分析器将其排除在 producer 更新之外。

独立 VF 的最早有效依赖间隔如下，单位为 cycle。其他源均已完成的样本用于计算各位置最小值。

| 指令 | 第一数据源 | 第二数据源 | mask | pair 配置 |
| --- | ---: | ---: | ---: | ---: |
| PNOT | 2 | 不适用 | 2 | 2 |
| PXOR | 3 | 3 | 2 | 3 |
| PAND（PMOV lowering） | 3（Pn/Pm 同 producer） | 同左 | 2 | 3 |
| PSEL | 3 | 3 | 2 | 3 |

当前 forwarding 配置按 opcode/form pair 保存一个值，使用已测源位置最小间隔的最大值。
PXOR、PAND、PSEL 的 mask 链因此采用 3-cycle 的保守近似。
PAND 证据覆盖 PMOV 的数据源合用形式；两个不同数据 producer 的进一步组合保留为扩展实验。
不同 opcode 之间的 forwarding 和交叉 II 继续按缺失参数策略报告 warning。

PNOT 在 EXU0/EXU1 上均观察到同端口相邻 1 cycle 的独立发射，确认 self-II=1。
其余三个指令的独立流观察值为 2 cycle；当前四流样本未证明所有源在首条发射前已经完成，
保留观测值和就绪检查，不写入已确认 II。VV_ALL 控制实验也未满足该检查。

合并四组指令的早期探针存在交叉竞争，部分 RAW 间隔升高；该运行作为对照保存。
参数使用拆分 VF 后的隔离样本，源位置链在带屏障控制中复现。

## 复现与证据

```bash
python3 skills/vfsim-instruction-benchmark/scripts/compute_sweep.py \
  --out results/predicate_logic_single \
  --operations pnot pxor pmov pmov_unmasked psel
python3 skills/vfsim-instruction-benchmark/scripts/memory_probe.py \
  --kind predicate_logic_matrix --iterations 1
python3 skills/vfsim-instruction-benchmark/scripts/analyze_predicate_logic.py \
  ARTIFACTS --output results/predicate_logic_analysis.json
```

[独立 VF 事件表](evidence/a5-predicate-logic/isolated.json) 和
[屏障控制事件表](evidence/a5-predicate-logic/barrier-control.json) 保存动态 ID、日志行号、端口、
各源 RAW 样本、就绪检查及 SHA256。原始源码、输入/golden/output、ELF、命令和日志归档在
仓库本地 `results/a5_predicate_logic_20261019/`；`manifest.json` 记录原始目录及文件校验值。

控制探针使用 `vnop()` 时，编译器报告其 builtin 不支持当前 target feature。
该失败样本保存在 `failed_vnop_probe/`，未产生任何参数。
