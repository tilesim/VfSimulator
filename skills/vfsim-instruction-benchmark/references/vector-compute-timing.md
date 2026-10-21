# A5 普通向量计算测量

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`，编译参数使用
`dav-c310-vec`、`__NPU_ARCH__=3101`，关闭 VF fusion 和向量指令重调度。
机器可读结果保存在 [向量计算证据](evidence/a5-vector-compute.json)，完整运行产物保存在
`results/a5_vector_compute_20261019/`。

## 指令与数据类型

探针覆盖普通一元、二元、向量—标量运算、累加、六种比较关系及对应的标量比较。
dtype 包括 FP16、FP32、BF16、8/16/32-bit 有符号和无符号整数；位运算另含
F8E4M3、F8E5M2、F8E8M0。每个组合依据目标头文件选择，单独进行目标编译。
`VSADD/VSADDS/VSSUB.int16` 的声明被该目标编译器拒绝，保留编译诊断。

每个用例使用独立 VF、输入窗口和输出窗口。四路独立结果、两次 RAW 结果、部分 mask
结果全部保存，最后 256 字节保护区逐字节比较。比较指令保存完整谓词 bitmap，
按数据元素的字节步长构造有效 bit。整数和位运算严格比较；浮点使用独立标量运算
和对应 dtype 的舍入，按记录的容差比较。

## Lowering 与 mask

- `VMULS.bf16` 调用展开为标量广播和 `VMUL.bf16`。运行记录包含实际的 `RV_VDUPS`
  和 `RV_VMUL`，单条 `VMULS.bf16` 的 latency 不作为配置项。广播在编译产物中可能被
  跨调用合并；CCE adapter 按调用构造展开序列。
- 该 CANN build 的 `VNOT.bf16` 编码使用 B8 predicate 粒度。B8 mask 用例校验全部
  256 字节及半向量清零；B16 mask 对照用例保留高字节失活的结果。
- `VSHLS/VSHRS` 使用运行时标量验证寄存器编码；字面量调用另验证 `VSHLI/VSHRI`
  编码。两种编码按 CCE 接口归并，latency 分布一致时才形成 form 参数。
- 无符号向量移位使用同宽的有符号移位量向量。移位量源探针选取明确范围内的非负值，
  独立检查 Vn 和 Vm 两条 RAW 路径。

## 时序提取

事件保留运行目录、VF PC 区间、动态 ID、opcode、寄存器、日志行号和 EXU 端口。
计算指令的 PERF 起点应等于 popped 加 1；latency 使用同 ID 的 done 减 popped。
`SEND/SMEM_BAR` 的控制轮询记录单独排除，计算或访存事件的重复 ID 会终止分析。

self-forwarding 分别收集 Vn、Vm、隐式旧 Vd 或 Pg 的依赖间隔。其他源在 producer
发射前已完成，或已被同一 opcode 的 producer 在相同源位置消费，才能用于该间隔。
所需源位置齐全时，将各位置最小值的最大值写入单值 pair；证据保留各位置差异。
累加探针分别让旧目的值、左源和右源构成依赖，所有结果均参与 golden。

II 使用同端口相邻且无 RAW 的指令。1-cycle 样本提供该模型粒度下的下界。
更大的 II 使用共同谓词门：先将各路输入写出，执行内存屏障，再加载共同谓词。
输入向量的完成事件和上一条指令对共同源的消费共同证明源已就绪；两个端口都需有
重复的有效最小间隔。屏障本身只约束内存操作。

普通访存记录包含实际 `VLDI/VLDS`、`VSTI/VSTS` 编码。LOAD 使用对齐的完整向量
NORM 访问；STORE 的基础 latency 仅收集源已完成的样本。批量 STORE 观测到的
producer→consumer 间隔保留为上界，独立 STORE 转发扫描继续补充。

端口同时出现 EXU0/EXU1 的稳定 latency 样本支持 `EXU01`。BF16 标量广播的端口
记录来自展开用例，寄存器源 `POS_LOWEST` 变体需要独立测量。

## 运行

在项目根目录执行，输出路径应为新目录：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/vector_compute.py \
  --out /tmp/a5-vector-compute --batch-size 32
python3 skills/vfsim-instruction-benchmark/scripts/vector_compute.py \
  --out /tmp/a5-vector-ready --operations vadd vsub vmul vadds vmuls \
  --warm-operands --batch-size 32
python3 skills/vfsim-instruction-benchmark/scripts/vector_compute.py \
  --out /tmp/a5-vector-sources --operations vmula vmadd vaxpy \
  --warm-operands --accumulator-sources
python3 skills/vfsim-instruction-benchmark/scripts/vector_compute.py \
  --out /tmp/a5-vector-shift-count --operations vshl vshr \
  --warm-operands --chain-source rhs
python3 skills/vfsim-instruction-benchmark/scripts/analyze_vector_compute.py \
  --runs /tmp/a5-vector-compute /tmp/a5-vector-ready \
    /tmp/a5-vector-sources /tmp/a5-vector-shift-count \
  --out /tmp/a5-vector-evidence.json --compact
```

分析器默认输出建议。`--apply` 新增有证据的配置，已有不同值保留为冲突记录。
完成源位置和历史证据核对后，可通过 `--replace` 指定需要重新标定的精确参数路径。
配置文件不使用未验证的 fallback 充当已测参数。

各调用变体、merging、软件双向量展开、转换和复杂访存的覆盖范围见
[覆盖进度](../../../docs/instruction_coverage/a5/progress.md)。
