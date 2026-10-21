# A5 转换、打包与解包测量

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`，宏 `__NPU_ARCH__=3101`。
完整运行产物位于 `results/a5_vector_conversion_20261019/`，
按动态 ID 配对的事件与参数见 [机器证据](evidence/a5-vector-conversion.json)。

## 覆盖与数值检查

37 种 masked VCVT、4 种 VPACK、4 种 VUNPACK 具有 latency、self-II 和 self-forwarding。
13 批 CAModel 实验中，143 个用例通过字节级输出和保护区校验，15 个诊断用例不参与标定。
VCVT 的 CCE 签名逐类型对声明，使用其实际的舍入、饱和、分段和可选 ZEROING 参数。
原生 64-bit 转换使用一个 256-byte 向量寄存器，2xVL 软件结构另行覆盖。

- 宽化从源中按 EVEN/ODD 或 P0..P3 选择交错 lane；窄化将结果写入相应交错位置。
- 全向量和部分 mask 同时检查有效元素与置零区域。浮点到整数使用独立的 R/A/F/C/Z 参考舍入。
- 浮点目的类型使用可精确表示的输入；浮点输出的边界舍入与特殊值继续单独测试。
- VPACK 检查 LOWER/HIGHER 的 128 个有效输出字节，其他输出字节保持输入镜像。
  RAW 链按源的有效区域收缩到 64/32 字节，避免使用未定义 lane 作为数值依据。
- VUNPACK 检查全部结果元素的符号扩展或零扩展。
- 各用例使用 4096-byte 输入/输出窗口，覆盖四条独立流、两条 RAW 和部分 mask 或分段对照。

## 时序提取

latency 为同一动态指令的 `done - popped`，EXU PERF 起点逐条核对为 `popped + 1`。
目标指令为 `RV_VCVT_F2F/F2I/I2F/I2I`、`RV_VPACK`、`RV_VSUNPACK/VZUNPACK`。
源码调用次数与实际 RV 数量分别记录，融合、展开、删除及数值失败样本不产生指令参数。

RAW 使用同类型对的物理指令，通过显式寄存器位视图连接 `Vd → Vn`。
链使用全零有效输入，所有中间类型表示有效且可独立验证。共同谓词在源数据存储和
内存屏障后加载；非谓词接口使用受该谓词控制的 VOR 开放源。分析排除其他源等待，
self-II 按同一端口上无 RAW、源已就绪的相邻事件统计。
转换在 EXU0 和 EXU1 均有样本；pack/unpack 的 EXU0 放置具有交织指令竞争记录。

VPACK 的观察用 VOR 与结果存储保留物理 pack，避免 PK store 融合。
独立流和 RAW 之间的发射间隔按具体源依赖记录，配置使用重复实验的最小有效间隔。

## 参数

| 指令 / form | latency | self-II | self-forwarding | 端口 |
| --- | ---: | ---: | ---: | --- |
| VCVT_BF16_TO_F16.bf16_to_f16 | 8 | 2 | 4 | EXU01 |
| VCVT_BF16_TO_F32.bf16_to_f32 | 7 | 1 | 3 | EXU01 |
| VCVT_BF16_TO_S32.bf16_to_s32 | 8 | 2 | 4 | EXU01 |
| VCVT_F16_TO_BF16.f16_to_bf16 | 8 | 2 | 4 | EXU01 |
| VCVT_F16_TO_F32.f16_to_f32 | 7 | 1 | 3 | EXU01 |
| VCVT_F16_TO_S16.f16_to_s16 | 8 | 2 | 4 | EXU01 |
| VCVT_F16_TO_S32.f16_to_s32 | 8 | 2 | 4 | EXU01 |
| VCVT_F16_TO_S8.f16_to_s8 | 8 | 2 | 4 | EXU01 |
| VCVT_F16_TO_U8.f16_to_u8 | 8 | 2 | 4 | EXU01 |
| VCVT_F32_TO_BF16.f32_to_bf16 | 7 | 1 | 3 | EXU01 |
| VCVT_F32_TO_F16.f32_to_f16 | 7 | 1 | 3 | EXU01 |
| VCVT_F32_TO_S16.f32_to_s16 | 8 | 2 | 4 | EXU01 |
| VCVT_F32_TO_S32.f32_to_s32 | 8 | 2 | 4 | EXU01 |
| VCVT_F32_TO_S64.f32_to_s64 | 8 | 2 | 4 | EXU01 |
| VCVT_S16_TO_F16.s16_to_f16 | 8 | 2 | 4 | EXU01 |
| VCVT_S16_TO_F32.s16_to_f32 | 8 | 2 | 4 | EXU01 |
| VCVT_S16_TO_S32.s16_to_s32 | 7 | 2 | 3 | EXU01 |
| VCVT_S16_TO_U32.s16_to_u32 | 7 | 2 | 3 | EXU01 |
| VCVT_S16_TO_U8.s16_to_u8 | 7 | 2 | 3 | EXU01 |
| VCVT_S32_TO_F32.s32_to_f32 | 8 | 2 | 4 | EXU01 |
| VCVT_S32_TO_S16.s32_to_s16 | 7 | 2 | 3 | EXU01 |
| VCVT_S32_TO_S64.s32_to_s64 | 7 | 2 | 3 | EXU01 |
| VCVT_S32_TO_U16.s32_to_u16 | 7 | 2 | 3 | EXU01 |
| VCVT_S32_TO_U8.s32_to_u8 | 7 | 2 | 3 | EXU01 |
| VCVT_S64_TO_F32.s64_to_f32 | 8 | 2 | 4 | EXU01 |
| VCVT_S64_TO_S32.s64_to_s32 | 7 | 2 | 3 | EXU01 |
| VCVT_S8_TO_F16.s8_to_f16 | 8 | 2 | 4 | EXU01 |
| VCVT_S8_TO_S16.s8_to_s16 | 7 | 2 | 3 | EXU01 |
| VCVT_S8_TO_S32.s8_to_s32 | 7 | 2 | 3 | EXU01 |
| VCVT_U16_TO_U32.u16_to_u32 | 7 | 2 | 3 | EXU01 |
| VCVT_U16_TO_U8.u16_to_u8 | 7 | 2 | 3 | EXU01 |
| VCVT_U32_TO_S16.u32_to_s16 | 7 | 2 | 3 | EXU01 |
| VCVT_U32_TO_U16.u32_to_u16 | 7 | 2 | 3 | EXU01 |
| VCVT_U32_TO_U8.u32_to_u8 | 7 | 2 | 3 | EXU01 |
| VCVT_U8_TO_F16.u8_to_f16 | 8 | 2 | 4 | EXU01 |
| VCVT_U8_TO_U16.u8_to_u16 | 7 | 2 | 3 | EXU01 |
| VCVT_U8_TO_U32.u8_to_u32 | 7 | 2 | 3 | EXU01 |
| VPACK.s16_to_u8 | 11 | 1 | 8 | EXU0_ONLY |
| VPACK.s32_to_u16 | 11 | 1 | 8 | EXU0_ONLY |
| VPACK.u16_to_u8 | 11 | 1 | 8 | EXU0_ONLY |
| VPACK.u32_to_u16 | 11 | 1 | 8 | EXU0_ONLY |
| VUNPACK.s16_to_s32 | 11 | 1 | 8 | EXU0_ONLY |
| VUNPACK.s8_to_s16 | 11 | 1 | 8 | EXU0_ONLY |
| VUNPACK.u16_to_u32 | 11 | 1 | 8 | EXU0_ONLY |
| VUNPACK.u8_to_u16 | 11 | 1 | 8 | EXU0_ONLY |

配置共新增 268 个字段。32 条 B32 配置继承记录映射到相同 RV_VPACK B32 编码的
`s32_to_u16/u32_to_u16` form，保留已有跨指令时序；机器证据单独记录其来源。
异种 opcode/form 的新转发和 II 继续独立测量。

## 诊断范围

- 2 个 pack 对照中，编译器将转换并入 PK store，输出的未选中半区没有定义。
  这些对照的实际 pack 数量与数值合同不满足单指令测量要求。
- 5 个有符号到无符号的负数对照中，目标结果限幅到零，诊断 golden 按模数截断。
  有效探针对 unsigned 结果显式采用非负下界。
- 8 个整数窄化的 RS_DISABLE 越界对照在该 CAModel 下仍呈现上下界限幅，
  与截断参考值不一致；完整输入、golden、实际输出和 sat 编码保留在原始产物。
  这些差异尚不足以推广为硬件语义结论。

FP8/FP4/HIF8、2xVL、fast/unmasked 转换接口、MODE_MERGING 和浮点边界舍入继续按
[覆盖进度](../../../docs/instruction_coverage/a5/progress.md) 跟踪。

## 复现

```bash
python3 skills/vfsim-instruction-benchmark/scripts/vector_conversion.py \
  --out /tmp/a5-conversion --jobs 3 --port-pressure --rounding-values
python3 skills/vfsim-instruction-benchmark/scripts/vector_conversion.py \
  --out /tmp/a5-conversion-odd --jobs 3 --part PART_ODD --pp PART_P3 \
  --pack-part HIGHER --round ROUND_Z --saturation RS_DISABLE --rounding-values
python3 skills/vfsim-instruction-benchmark/scripts/vector_conversion.py \
  --out /tmp/a5-conversion-bounds --operations vcvt --forms s32_to_u8 u32_to_u16 \
  --boundary-values --saturation RS_DISABLE
python3 skills/vfsim-instruction-benchmark/scripts/analyze_vector_conversion.py \
  --runs /tmp/a5-conversion /tmp/a5-conversion-odd /tmp/a5-conversion-bounds \
  --out /tmp/a5-conversion-evidence.json --inherit-pack-b32
```

数值或物理指令数量不满足合同时，生成器返回失败并保留全部产物。
参数分析器剔除这些记录，`--apply` 写入合格参数；已有不同值通过 `--replace` 精确指定。

## 工程验证

377 项 Python 测试和 8 项 CTest 通过，包含 CCE/JSON/Canonical 完整输出的 Python/C++
对照。22 个 Native smoke 用例通过，周期与现有基线一致。
