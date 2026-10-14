# A5 谓词寄存器压力实验

日期：2026-09-22。代码分支：`vfinfo-core-api-unification`，基准提交 `b534075`。
使用 CANN 9.0.0-beta.1、`dav-c310-vec`、CAModel `Ascend950PR_9599`。
本实验未修改 VfSim 的资源模型。

## 设计

每个 VF 有一个 8 次迭代的 for 循环，不做 unroll。每轮读取不同的 64 个
FP32 输入，先生成 N 个比较谓词，再逐个 vsel 消费并累加结果。
始终保留一个全有效谓词，因此源码峰值需要 N+1 个谓词值。

```text
all = pset(PAT_ALL)
acc = 0
for i in 0..7:
    a = load(input + 64*i)
    p0 = a > threshold0
    ...
    pNminus1 = a > thresholdNminus1
    acc += select(p0, 1, 0)
    ...
    acc += select(pNminus1, 1, 0)
store(acc)
```

普通重复循环不会扩大架构寄存器的静态需求，因此需要同时增加每轮活跃的
谓词值。8 次动态迭代则用于观察同一架构编号的物理重命名。
golden 独立计算每个 lane 在 8 轮、N 个阈值下比较成立的次数。

## 结果

下面指令数是整个 VF 的动态数量，包含全部 8 轮。源码没有 PST、PLD 或 membar。

| 比较 mask 数 N | 全有效 mask | PSTI | PLDI | SMEM_BAR | 最低可用物理 P | 数值校验 |
|---:|---:|---:|---:|---:|---:|---|
| 4 | 1 | 0 | 0 | 0 | 7 | 64/64 逐位通过 |
| 5 | 1 | 0 | 0 | 0 | 5 | 64/64 逐位通过 |
| 6 | 1 | 0 | 0 | 0 | 5 | 64/64 逐位通过 |
| 7 | 1 | 16 | 16 | 32 | 5 | 64/64 逐位通过 |
| 8 | 1 | 24 | 24 | 48 | 5 | 64/64 逐位通过 |
| 16 | 1 | 88 | 88 | 176 | 2 | 64/64 逐位通过 |
| 32 | 1 | 216 | 216 | 432 | 0 | 64/64 逐位通过 |

## 可以确认的结论

1. 本编译器在该 VF 中使用架构谓词 `P1..P7`。6 个比较 mask 加 `all`
   可以全部留在寄存器里；7 个比较 mask 加 `all` 开始 spill。
   因此本测试证明的可分配容量为 7 个。`P0` 没有被使用，其保留/特殊语义
   以及架构总数是否为 8 尚需 ISA 文档确认，不能仅凭最大编号断言。
2. 谓词 spill 使用 `RV_PSTI` / `RV_PLDI`，不是普通 `VST` / `VLD`。
   编译器同时插入 `RV_SMEM_BAR`，包含 VLD_VST、VST_VLD。
   N=7 时不是只 spill 一个值，而是每轮各两条 PSTI/PLDI；这是观测到的
   编译器分配结果，不能简单用 spill 数倒推架构容量。
3. 物理谓词编号覆盖 `0..31`，IDU 初始可用数为 32，和 vreg 独立计数。
   N=32 时出现 60 条 PERF IDU_BLOCK 记录包含 `OOO no avail phy preg`，
   该数是阻塞记录数，不是唯一阻塞周期数。
   第一个事件 cycle 1732 为 `preg:0 vreg:64`，直接证明了谓词资源可独立耗尽。
4. N=4 的 ISU 日志中，同一架构 P2 在 cycle 1700/1703/1705 分别被映射到
   物理 P1/P5/P9，证明跨迭代保留了不同动态版本。
5. N=4 中 VSEL(id=71) 在 1709 发射，1712 出现其 P2/physical1 的 DEC_SRC
   释放事件。这是局部观测，不足以据此确定通用释放延迟和 IDU credit 可见延迟。

## 复现与归档

```bash
python3 cce_code/predicate_select_test/pressure.py --counts 4 5 6 7 8 16 32 --run 4 5 6 7 8 16 32
```

运行脚本输出唯一的 `/tmp/vfsim-predicate-pressure-*` 路径。
使用 `analyze_pressure.py <运行目录...> --out <新的归档目录>` 生成汇总并归档。

本次归档：`results/predicate_pressure_a5_20260922/`。
每个 `nN/` 包含 `predicate_pressure.cce`、原始输入、golden、输出、编译命令、
core0.veccore0 日志和 `validation_and_analysis.json`。根目录为 `summary.json`。
结果目录可能被 Git 忽略，可使用上述受版本管理的脚本重新生成。
