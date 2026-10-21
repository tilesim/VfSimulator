# A5 归约、多结果与选择指令测量

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`，
宏 `__NPU_ARCH__=3101`，关闭 VF fusion 和向量指令重调度。
机器证据保存在 [结构计算证据](evidence/a5-vector-structural.json)，完整运行产物位于
`results/a5_vector_structural_20261019/`。

## 指令语义与 golden

- `VCADD`：FP16、FP32 及 16/32-bit 有符号、无符号整数。16-bit 整数输入生成
  32-bit 累加结果，Catalog 使用源 dtype 选择 form。
- `VCMAX/VCMIN`：上述六种输入类型；校验归约值和整数位表示的索引。
- `VCGADD/VCGMAX/VCGMIN`：按 32 字节块归约，校验八组结果。整数 16-bit 的
  `VCGADD` 将八个结果编码为 32-bit 值，CCE API 以 `vector_s16/u16` 接收这些寄存器位。
- `VCPADD`：FP16/FP32 相邻元素求和，校验全部有效结果。
- `VADDC/VADDCS/VSUBC/VSUBCS`：S32/U32，校验向量结果和完整 carry bitmap。
  减法 carry 表示无借位；带 carry 的减法为 `lhs - rhs - (1 - carry_in)`。
- `VMULL`：S32/U32，分别校验乘积的低 32-bit 与高 32-bit。
- `VINTLV/VDINTLV`：FP16、FP32、BF16、8/16/32-bit 整数及三种 FP8，逐字节检查两个结果。
- `VSEL`：同一组十二种类型，使用运行时交替 mask 检查两路数据选择。

每个 VF 使用独立的 4096-byte 输入和输出窗口，包含四路独立结果、两次 RAW 结果，
以及部分 mask 或交换输入对照。输出窗口从输入镜像初始化；有效结果、间隙和末尾
512-byte 保护区全部逐字节比较。浮点归约使用可精确表示的输入，避免依赖求和树次序。
归约只保存定义明确的结果；`VCPADD` 两次 RAW 分别读取四个和两个有效源元素。

## 时序与依赖

latency 由相同动态 ID 的 `done - popped` 得到，EXU PERF 起点核对为 `popped + 1`。
日志中的 `Vd` 表示配对结果的首个寄存器；`VMULL/VINTLV/VDINTLV` 的第二个结果
位于相邻寄存器，分析器为其建立独立 producer 标记 `Vd1`。

`VADDCS/VSUBCS` 的 carry-in 在功能语义上使用旧 `Pd`。编译器可能为寄存器分配插入
`PAND` 谓词复制；这些事件保留在原始日志中。隔离用例在屏障前准备不同的 carry
目的值，保存它们并确认完成，再加载共同 mask，让独立目标流同时开放。
寄存器分配产生的复制数量取决于编译上下文；指令参数对应实际目标 RV 编码。

该 CANN build 的 IDU dispatch 源清单只列出 Pg，未登记旧 Pd carry-in。
纯 carry 链可以在两个端口同周期发射。分析器将这类间隔保存为调度器未跟踪的
依赖观测，禁止用它生成 0-cycle forwarding。S32/U32 的 `VADDCS/VSUBCS` 各 form
具有 latency、self-II、向量与 mask 路径测量；旧 Pd 路径保留为未确认项。

self-forwarding 按 producer 结果和 consumer 源位置分类。双结果指令分别覆盖
`Vd/Vd1 → Vn/Vm`；进位指令覆盖向量结果到两个向量源、carry 结果到 mask，
带 carry 输入的指令另覆盖 `Pd → 旧 Pd`。只有全部路径满足其他源就绪条件时，
才将各路径最小间隔的最大值写入单值 forwarding pair。证据保留每条路径的差异。

self-II 使用同端口相邻且无 RAW 的实际目标指令。大于一周期的值需要共同就绪门，
以及对应端口上可重复的有效最小间隔。双端口指令分别检查 EXU0 和 EXU1。
端口竞争用例加入四条具有可观察结果的交织指令；`EXU0_ONLY` 表达该 CAModel
目标在独立流和竞争条件下的放置行为。ALU/SFU 为 VFSim 的功能分类。

## 参数表

forwarding 列为该 form 全部已确认路径的单值聚合，单位为 cycle。

| 指令 | dtype | latency | self-II | self-forwarding | 端口 |
| --- | --- | ---: | ---: | --- | --- |
| VADDC | int32, uint32 | 7 | 2 | 3 | EXU01 |
| VADDCS | int32, uint32 | 7 | 2 | 向量/Pg=4；旧 Pd 未确认 | EXU01 |
| VCADD | int16, int32, uint16, uint32 | 16 | 1 | 13 | EXU0_ONLY |
| VCADD | fp32 | 22 | 1 | 19 | EXU0_ONLY |
| VCADD | fp16 | 24 | 1 | 21 | EXU0_ONLY |
| VCGADD | int16, int32, uint16, uint32 | 13 | 1 | 10 | EXU0_ONLY |
| VCGADD | fp32 | 16 | 1 | 13 | EXU0_ONLY |
| VCGADD | fp16 | 18 | 1 | 15 | EXU0_ONLY |
| VCGMAX | fp32, int32, uint32 | 13 | 1 | 10 | EXU0_ONLY |
| VCGMAX | fp16, int16, uint16 | 14 | 2 | 11 | EXU0_ONLY |
| VCGMIN | fp32, int32, uint32 | 13 | 1 | 10 | EXU0_ONLY |
| VCGMIN | fp16, int16, uint16 | 14 | 2 | 11 | EXU0_ONLY |
| VCMAX | fp32, int32, uint32 | 16 | 1 | 13 | EXU0_ONLY |
| VCMAX | fp16, int16, uint16 | 17 | 2 | 14 | EXU0_ONLY |
| VCMIN | fp32, int32, uint32 | 16 | 1 | 13 | EXU0_ONLY |
| VCMIN | fp16, int16, uint16 | 17 | 2 | 14 | EXU0_ONLY |
| VCPADD | fp16, fp32 | 12 | 1 | 9 | EXU0_ONLY |
| VDINTLV | bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11 | 2 | 9 | EXU0_ONLY |
| VINTLV | bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11 | 2 | 9 | EXU0_ONLY |
| VMULL | int32, uint32 | 8 | 2 | 5 | EXU01 |
| VSEL | bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 6 | 1 | 2 | EXU01 |
| VSUBC | int32, uint32 | 7 | 2 | 3 | EXU01 |
| VSUBCS | int32, uint32 | 7 | 2 | 向量/Pg=4；旧 Pd 未确认 | EXU01 |

## 运行与配置

```bash
python3 skills/vfsim-instruction-benchmark/scripts/vector_structural.py \
  --out /tmp/a5-structural --warm-operands --in-place-carry
python3 skills/vfsim-instruction-benchmark/scripts/vector_structural.py \
  --out /tmp/a5-structural-ports --operations vcadd vcmax vcmin vcgadd vcgmax vcgmin vcpadd \
  --warm-operands --port-pressure
python3 skills/vfsim-instruction-benchmark/scripts/vector_structural.py \
  --out /tmp/a5-structural-high --operations vmull vintlv vdintlv \
  --warm-operands --producer-result 1 --chain-source rhs
python3 skills/vfsim-instruction-benchmark/scripts/vector_structural.py \
  --out /tmp/a5-structural-carry --operations vaddcs vsubcs \
  --warm-operands --in-place-carry --chain-source carry
python3 skills/vfsim-instruction-benchmark/scripts/analyze_vector_structural.py \
  --runs /tmp/a5-structural /tmp/a5-structural-ports /tmp/a5-structural-high /tmp/a5-structural-carry \
  --out /tmp/a5-structural-evidence.json
```

完整 self-forwarding 需要组合两个结果位置、两个向量源位置，以及进位的 mask/carry
来源用例。分析器报告尚未齐全的路径；`--apply` 只添加满足证据条件的参数。
已有不同值记录为冲突，经过核对可用 `--replace` 指定精确参数路径。

MODE_MERGING、64-bit/2xVL 展开、异种 opcode/form 的 forwarding/II，以及计算到
STORE 的独立扫描，继续按 [覆盖进度](../../../docs/instruction_coverage/a5/progress.md) 记录。
