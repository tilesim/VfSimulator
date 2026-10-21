# A5 SIMD VF 指令覆盖清单

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、编译目标 `dav-c310-vec`，宏 `__NPU_ARCH__=3101`。
输入清单来自该目标预处理后的 VF 头文件。标量计算、SIMT、Cube、DMA 和标量特殊寄存器配置接口在统计范围外。
带标量源的向量计算仍计入范围。

[覆盖进度与测量限制](instruction_coverage/a5/progress.md) 给出数值实验、软件展开和时序证据；
[逐签名检查](instruction_coverage/a5/signatures.json) 保存代表性调用的 adapter 校验结果。
签名检查只选择一个合法参数配置；各 mode、mask、舍入和索引分布的数值覆盖分别记录在 benchmark 中。

## 统计

| 项目 | 数量 |
| --- | ---: |
| 非标量 VF API 名称 | 160 |
| 去重 CANN 签名 | 1426 |
| API/form 组合 | 1091 |
| 通过 adapter 检查的签名 | 1422 |
| 该编译目标拒绝的 API 名称 | 7 |
| Catalog opcode（含虚拟入口） | 227 |
| 非虚拟 opcode/form（含兼容位宽 form） | 1117 |
| 类型及物理辅助 form | 1011 |
| 具有显式 latency 的 form | 981 |
| 类型及物理辅助 form 缺少 latency | 41 |
| 类型及物理辅助 COMPUTE form 缺少 self-II | 11 |
| forwarding 配置 pair（含 wildcard） | 1699 |
| II 配置 pair（含 wildcard） | 2506 |

## CCE 接口

每条展开规则生成独立硬件指令及 RAW 依赖。双向量占用两个普通向量值；VAG 的循环步幅保存在访存地址表达式中。
`physical_lowering_forms` 保存代表性调用生成的物理 form，完整签名、头文件位置和配置哈希见机器清单。

| API | 类别 | CANN form | adapter 签名 | 状态 |
| --- | --- | --- | ---: | --- |
| `chistv2` | vector | uint16 | 1/1 | 已适配 |
| `dhistv2` | vector | uint16 | 1/1 | 已适配 |
| `mem_bar` | control | VLD_VST, VST_VLD, VST_VST, VV_ALL | 1/1 | 已适配 |
| `movvp` | vector | uint16, uint32 | 2/2 | 已适配 |
| `pand` | predicate | b8 | 1/1 | 已适配 |
| `pdintlv_b16` | predicate | b16 | 1/1 | 已适配 |
| `pdintlv_b32` | predicate | b32 | 1/1 | 已适配 |
| `pdintlv_b8` | predicate | b8 | 1/1 | 已适配 |
| `pge_b16` | predicate | b16 | 1/1 | 已适配 |
| `pge_b32` | predicate | b32 | 1/1 | 已适配 |
| `pge_b8` | predicate | b8 | 1/1 | 已适配 |
| `pintlv_b16` | predicate | b16 | 1/1 | 已适配 |
| `pintlv_b32` | predicate | b32 | 1/1 | 已适配 |
| `pintlv_b8` | predicate | b8 | 1/1 | 已适配 |
| `pld` | memory | b8 | 1/1 | 已适配 |
| `pldi` | memory | b8 | 2/2 | 已适配 |
| `plds` | memory | b8 | 2/2 | 已适配 |
| `plt_2xvl_b64` | predicate | b32 | 1/1 | 已适配 |
| `plt_b16` | predicate | b16 | 1/1 | 已适配 |
| `plt_b32` | predicate | b32 | 1/1 | 已适配 |
| `plt_b8` | predicate | b8 | 1/1 | 已适配 |
| `pltm_2xvl_b64` | predicate | b32 | 1/1 | 已适配 |
| `pltm_b16` | predicate | b16 | 1/1 | 已适配 |
| `pltm_b32` | predicate | b32 | 1/1 | 已适配 |
| `pltm_b8` | predicate | b8 | 1/1 | 已适配 |
| `pmov` | predicate | b8 | 2/2 | 已适配 |
| `pnot` | predicate | b8 | 1/1 | 已适配 |
| `por` | predicate | b8 | 1/1 | 已适配 |
| `ppack` | predicate | b8 | 1/1 | 已适配 |
| `psel` | predicate | b8 | 1/1 | 已适配 |
| `pset_2xvl_b64` | predicate | b32 | 1/1 | 已适配 |
| `pset_b16` | predicate | b16 | 1/1 | 已适配 |
| `pset_b32` | predicate | b32 | 1/1 | 已适配 |
| `pset_b8` | predicate | b8 | 1/1 | 已适配 |
| `pslide_b16` | predicate | b16 | 1/1 | 目标编译器拒绝 |
| `pslide_b32` | predicate | b32 | 1/1 | 目标编译器拒绝 |
| `pslide_b8` | predicate | b8 | 1/1 | 目标编译器拒绝 |
| `pst` | memory | b8 | 1/1 | 已适配 |
| `psti` | memory | b8 | 2/2 | 已适配 |
| `psts` | memory | b8 | 2/2 | 已适配 |
| `pstu` | memory | uint16, uint32 | 2/2 | 已适配 |
| `punpack` | predicate | b8 | 1/1 | 已适配 |
| `pxor` | predicate | b8 | 1/1 | 已适配 |
| `vabs` | vector | 2xvl_s64, fp16, fp32, int16, int32, int8 | 6/6 | 已适配 |
| `vabsdif` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vadd` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vaddc` | vector | int32, uint32 | 2/2 | 已适配 |
| `vaddcs` | vector | int32, uint32 | 2/2 | 已适配 |
| `vadds` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vag_b16` | address | b16 | 4/4 | 已适配 |
| `vag_b32` | address | b32 | 4/4 | 已适配 |
| `vag_b8` | address | b8 | 4/4 | 已适配 |
| `vand` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vaxpy` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vbr` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vcadd` | vector | 2xvl_s64, 2xvl_u64, fp16, fp32, int16, int32, uint16, uint32 | 8/8 | 已适配 |
| `vcgadd` | vector | fp16, fp32, int16, int32, uint16, uint32 | 6/6 | 已适配 |
| `vcgmax` | vector | fp16, fp32, int16, int32, uint16, uint32 | 6/6 | 已适配 |
| `vcgmin` | vector | fp16, fp32, int16, int32, uint16, uint32 | 6/6 | 已适配 |
| `vci` | vector | 2xvl_s64, fp16, fp32, int16, int32, int8 | 6/6 | 已适配 |
| `vcmax` | vector | 2xvl_s64, 2xvl_u64, fp16, fp32, int16, int32, uint16, uint32 | 8/8 | 已适配 |
| `vcmin` | vector | 2xvl_s64, 2xvl_u64, fp16, fp32, int16, int32, uint16, uint32 | 8/8 | 已适配 |
| `vcmp_eq` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmp_ge` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmp_gt` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmp_le` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmp_lt` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmp_ne` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_eq` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_ge` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_gt` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_le` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_lt` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcmps_ne` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vcpadd` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vcvt` | vector | 2xvl_s64_to_2xvl_u64, 2xvl_s64_to_f32, 2xvl_s64_to_s32, 2xvl_u64_to_2xvl_s64, 2xvl_u64_to_u32, bf16_to_f16, bf16_to_f32, bf16_to_f4e1m2x2, bf16_to_f4e2m1x2, bf16_to_f8e4m3, bf16_to_f8e5m2, bf16_to_s32, f16_to_bf16, f16_to_f32, f16_to_f8e4m3, f16_to_f8e5m2, f16_to_hif8, f16_to_s16, f16_to_s32, f16_to_s4x2, f16_to_s8, f16_to_u8, f32_to_2xvl_s64, f32_to_bf16, f32_to_f16, f32_to_f8e4m3, f32_to_f8e5m2, f32_to_hif8, f32_to_s16, f32_to_s32, f32_to_s64, f4e1m2x2_to_bf16, f4e2m1x2_to_bf16, f8e4m3_to_f32, f8e5m2_to_f32, hif8_to_f16, hif8_to_f32, s16_to_f16, s16_to_f32, s16_to_s32, s16_to_s4x2, s16_to_u32, s16_to_u8, s32_to_2xvl_s64, s32_to_f32, s32_to_s16, s32_to_s64, s32_to_u16, s32_to_u8, s4x2_to_bf16, s4x2_to_f16, s4x2_to_s16, s64_to_f32, s64_to_s32, s8_to_f16, s8_to_s16, s8_to_s32, u16_to_u32, u16_to_u8, u32_to_2xvl_u64, u32_to_s16, u32_to_u16, u32_to_u8, u8_to_f16, u8_to_u16, u8_to_u32 | 81/81 | 已适配 |
| `vcvt_f162s4` | vector | f16_to_s4x2 | 1/1 | 已适配 |
| `vcvt_s162s4` | vector | s16_to_s4x2 | 1/1 | 已适配 |
| `vcvt_s42bf16` | vector | s4x2_to_bf16 | 1/1 | 已适配 |
| `vcvt_s42f16` | vector | s4x2_to_f16 | 1/1 | 已适配 |
| `vcvt_s42s16` | vector | s4x2_to_s16 | 1/1 | 已适配 |
| `vdintlv` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vdiv` | vector | 2xvl_s64, 2xvl_u64, fp16, fp32, int16, int32, uint16, uint32 | 8/8 | 已适配 |
| `vdup` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 28/28 | 已适配 |
| `vdupi_s16` | vector | int16 | 1/1 | 已适配 |
| `vdupi_s8` | vector | int8 | 1/1 | 已适配 |
| `vdupi_u16` | vector | uint16 | 1/1 | 已适配 |
| `vdupi_u8` | vector | uint8 | 1/1 | 已适配 |
| `vexp` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vexpdif` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vfcvt` | vector | f16_to_f32, f16_to_s16, f16_to_s32, f16_to_s8, f16_to_u8, f32_to_f16, f32_to_s16, f32_to_s32, s16_to_f16, s16_to_f32, s32_to_f32, s8_to_f16, u8_to_f16 | 13/13 | 已适配 |
| `vgather2` | memory | __cce_half, bf16, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint64, uint8 | 14/14 | 已适配 |
| `vgather2_bc` | memory | bf16, fp16, fp32, int16, int32, uint16, uint32 | 7/7 | 已适配 |
| `vgatherb` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint64, uint8 | 31/31 | 已适配 |
| `vintlv` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vld` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, s4x2, uint16, uint32, uint64, uint8 | 66/66 | 已适配 |
| `vlda` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vldas` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vlds` | memory | 2xvl_s64, 2xvl_u64, bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint64, uint8 | 66/66 | 已适配 |
| `vldu` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vldui` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vldus` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vln` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vload` | memory | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vlrelu` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vmadd` | vector | bf16, fp16, fp32 | 3/3 | 已适配 |
| `vmax` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vmaxs` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vmin` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vmins` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vmod` | vector | 2xvl_s64, 2xvl_u64, int16, int32, uint16, uint32 | 6/6 | 已适配 |
| `vmov` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 23/23 | 已适配 |
| `vmul` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, uint16, uint32 | 9/9 | 已适配 |
| `vmula` | vector | bf16, fp16, fp32, int16, int32, uint16, uint32 | 7/7 | 已适配 |
| `vmull` | vector | int32, uint32 | 2/2 | 已适配 |
| `vmuls` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, uint16, uint32 | 9/9 | 已适配 |
| `vmulscvt` | vector | f32_to_f16 | 1/1 | 已适配 |
| `vneg` | vector | 2xvl_s64, 2xvl_u64, fp16, fp32, int16, int32, int8 | 7/7 | 已适配 |
| `vnop` | control | control | 0/1 | 目标编译器拒绝 |
| `vnot` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vor` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vpack` | vector | s16_to_u8, s32_to_u16, u16_to_u8, u32_to_u16 | 4/4 | 已适配 |
| `vprelu` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vrelu` | vector | fp16, fp32, int32 | 3/3 | 已适配 |
| `vsadd` | vector | int16 | 0/1 | 目标编译器拒绝 |
| `vsadds` | vector | int16 | 0/1 | 目标编译器拒绝 |
| `vscatter` | memory | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vscvt` | vector | s16_to_u8, s32_to_s16, s32_to_u16, s32_to_u8, u16_to_u8, u32_to_s16, u32_to_u16, u32_to_u8 | 8/8 | 已适配 |
| `vsel` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |
| `vselr` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, int16, int32, int8, uint16, uint32, uint8 | 13/13 | 已适配 |
| `vselrv2` | vector | bf16, fp16, int16, uint16 | 4/4 | 已适配 |
| `vsfcvt` | vector | f16_to_s16, f16_to_s8, f16_to_u8, f32_to_s16, f32_to_s32 | 5/5 | 已适配 |
| `vshl` | vector | 2xvl_s64, 2xvl_u64, int16, int32, int8, uint16, uint32, uint8 | 8/8 | 已适配 |
| `vshls` | vector | 2xvl_s64, 2xvl_u64, int16, int32, int8, uint16, uint32, uint8 | 8/8 | 已适配 |
| `vshr` | vector | 2xvl_s64, 2xvl_u64, int16, int32, int8, uint16, uint32, uint8 | 8/8 | 已适配 |
| `vshrs` | vector | 2xvl_s64, 2xvl_u64, int16, int32, int8, uint16, uint32, uint8 | 8/8 | 已适配 |
| `vsldb` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vsqrt` | vector | fp16, fp32 | 2/2 | 已适配 |
| `vsqz` | vector | bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, hif8, int16, int32, int8, uint16, uint32, uint8 | 26/26 | 已适配 |
| `vsst` | memory | bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 9/9 | 已适配 |
| `vsstb` | memory | bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 24/24 | 已适配 |
| `vssub` | vector | int16 | 0/1 | 目标编译器拒绝 |
| `vst` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, s4x2, uint16, uint32, uint64, uint8 | 30/30 | 已适配 |
| `vsta` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vstai` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vstar` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vstas` | memory | bf16, control, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vstore` | memory | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vsts` | memory | 2xvl_s64, 2xvl_u64, bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint64, uint8 | 47/47 | 已适配 |
| `vstu` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vstui` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vstur` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 15/15 | 已适配 |
| `vstus` | memory | bf16, f4e1m2x2, f4e2m1x2, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int64, int8, uint16, uint32, uint8 | 30/30 | 已适配 |
| `vsub` | vector | 2xvl_s64, 2xvl_u64, bf16, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 11/11 | 已适配 |
| `vsubc` | vector | int32, uint32 | 2/2 | 已适配 |
| `vsubcs` | vector | int32, uint32 | 2/2 | 已适配 |
| `vtrc` | vector | bf16, fp16, fp32 | 3/3 | 已适配 |
| `vunpack` | vector | s16_to_s32, s8_to_s16, u16_to_u32, u8_to_u16 | 4/4 | 已适配 |
| `vusqz` | vector | int16, int32, int8, uint16, uint32, uint8 | 6/6 | 已适配 |
| `vxor` | vector | 2xvl_s64, 2xvl_u64, bf16, f8e4m3, f8e5m2, f8e8m0, fp16, fp32, int16, int32, int8, uint16, uint32, uint8 | 14/14 | 已适配 |

## 时序配置

显式配置值与实测覆盖分别记录。机器清单中的 `latency_evidence` 指向对应的实验；缺少该链接的已有配置不能据此认定为新增实测。
`b16/b32` 兼容 form 单列，CANN dtype 使用精确类型键。辅助 byte 访存 form 用于展开规则。
self-forwarding 只适用于具有合法同形 RAW 的指令。异种指令的 forwarding 依靠精确 pair、明确继承和缺失参数回退；
所有合法 producer/consumer 的笛卡尔积尚未逐对测量。GSU 与 VSSTB 的服务参数对应记录的索引/步幅分布。

| opcode.form | latency | FU / 端口 | self-II | 范围 |
| --- | ---: | --- | ---: | --- |
| `VXOR.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VXOR.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSUBCS.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUBCS.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUBC.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUBC.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSHR.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHR.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHR.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHR.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHR.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHR.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHL.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VRELU.fp16` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VRELU.fp32` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VRELU.int32` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VPRELU.fp16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VPRELU.fp32` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNOT.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VNEG.fp16` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VNEG.fp32` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VNEG.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VNEG.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VNEG.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULL.int32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULL.uint32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.bf16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.fp16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.fp32` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.int16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.int32` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.uint16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULA.uint32` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMADD.bf16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMADD.fp16` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VMADD.fp32` | 9 | ALU/EXU01 | 2 | typed_or_physical |
| `VLRELU.fp16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VLRELU.fp32` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VADDCS.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDCS.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDC.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDC.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VABSDIF.fp16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VABSDIF.fp32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `PNOT.b8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `PXOR.b8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `PSEL.b8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `MOVP_B16.b16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `MOVP_B32.b32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `PLDS.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `PSTS.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `PLT.b16` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PLT.b32` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PLT.b8` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PSTU.b16` | 8 | LSU | 不适用 | compatibility |
| `PSTU.b32` | 8 | LSU | 不适用 | compatibility |
| `PSTU.uint16` | 8 | LSU | 不适用 | typed_or_physical |
| `PSTU.uint32` | 8 | LSU | 不适用 | typed_or_physical |
| `VLDAS.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDAS.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VMOV.b32` | 6 | ALU/EXU01 | 1 | compatibility |
| `VMOV.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.int64` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VINTLV.b32` | 11 | ALU/EXU0_ONLY | — | compatibility |
| `VINTLV.bf16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.f8e4m3` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.f8e5m2` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.f8e8m0` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.fp16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.fp32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.int16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.int32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.int8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.uint16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.uint32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VINTLV.uint8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.b32` | 11 | ALU/EXU0_ONLY | — | compatibility |
| `VDINTLV.bf16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.f8e4m3` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.f8e5m2` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.f8e8m0` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.fp16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.fp32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.int16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.int32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.int8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.uint16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.uint32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VDINTLV.uint8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `MOVVP.b16` | 16 | ALU/EXU01 | — | compatibility |
| `MOVVP.b32` | 16 | ALU/EXU01 | — | compatibility |
| `MOVVP.uint16` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `MOVVP.uint32` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `PAND.b8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `POR.b8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `PSET_B8.b8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `PSET_B16.b16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `PSET_B32.b32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.b16` | — | —/— | — | compatibility |
| `VCMP_GT.b32` | — | —/— | — | compatibility |
| `VCMP_GT.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GT.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.b16` | — | —/— | — | compatibility |
| `VCMPS_GT.b32` | — | —/— | — | compatibility |
| `VCMPS_GT.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GT.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.b16` | — | —/— | — | compatibility |
| `VCMP_LT.b32` | — | —/— | — | compatibility |
| `VCMP_LT.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LT.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.b16` | — | —/— | — | compatibility |
| `VCMPS_LT.b32` | — | —/— | — | compatibility |
| `VCMPS_LT.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LT.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.b16` | — | —/— | — | compatibility |
| `VCMP_LE.b32` | — | —/— | — | compatibility |
| `VCMP_LE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_LE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.b16` | — | —/— | — | compatibility |
| `VCMPS_LE.b32` | — | —/— | — | compatibility |
| `VCMPS_LE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_LE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.b16` | — | —/— | — | compatibility |
| `VCMP_GE.b32` | — | —/— | — | compatibility |
| `VCMP_GE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_GE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.b16` | — | —/— | — | compatibility |
| `VCMPS_GE.b32` | — | —/— | — | compatibility |
| `VCMPS_GE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_GE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.b16` | — | —/— | — | compatibility |
| `VCMP_NE.b32` | — | —/— | — | compatibility |
| `VCMP_NE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_NE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.b16` | — | —/— | — | compatibility |
| `VCMPS_NE.b32` | — | —/— | — | compatibility |
| `VCMPS_NE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_NE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.b16` | — | —/— | — | compatibility |
| `VCMPS_EQ.b32` | — | —/— | — | compatibility |
| `VCMPS_EQ.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMPS_EQ.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAXPY.fp16` | 9 | ALU/EXU01 | 1 | typed_or_physical |
| `VAXPY.fp32` | 9 | ALU/EXU01 | 1 | typed_or_physical |
| `VADDS.b16` | — | —/— | — | compatibility |
| `VADDS.b32` | — | —/— | — | compatibility |
| `VADDS.bf16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADDS.fp16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADDS.fp32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADDS.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDS.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDS.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDS.uint16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDS.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADDS.uint8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VEXP.b16` | — | —/— | — | compatibility |
| `VEXP.b32` | — | —/— | — | compatibility |
| `VEXP.fp16` | 21 | SFU/EXU01 | 8 | typed_or_physical |
| `VEXP.fp32` | 16 | SFU/EXU01 | 4 | typed_or_physical |
| `VADD.b16` | — | —/— | — | compatibility |
| `VADD.b32` | — | —/— | — | compatibility |
| `VADD.bf16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADD.fp16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADD.fp32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VADD.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADD.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADD.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADD.uint16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADD.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VADD.uint8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULS.b16` | — | —/— | — | compatibility |
| `VMULS.b32` | — | —/— | — | compatibility |
| `VMULS.fp16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VMULS.fp32` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VMULS.int16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULS.int32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULS.uint16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMULS.uint32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VDIV.b16` | — | —/— | — | compatibility |
| `VDIV.b32` | — | —/— | — | compatibility |
| `VDIV.fp16` | 22 | SFU/EXU01 | 8 | typed_or_physical |
| `VDIV.fp32` | 17 | SFU/EXU01 | 4 | typed_or_physical |
| `VABS.b16` | — | —/— | — | compatibility |
| `VABS.b32` | — | —/— | — | compatibility |
| `VABS.fp16` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VABS.fp32` | 5 | ALU/EXU01 | 1 | typed_or_physical |
| `VABS.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VABS.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VABS.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.b16` | — | —/— | — | compatibility |
| `VSUB.b32` | — | —/— | — | compatibility |
| `VSUB.bf16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VSUB.fp16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VSUB.fp32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VSUB.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.uint16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.uint32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VSUB.uint8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VMUL.b16` | — | —/— | — | compatibility |
| `VMUL.b32` | — | —/— | — | compatibility |
| `VMUL.bf16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VMUL.fp16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VMUL.fp32` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VMUL.int16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMUL.int32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMUL.uint16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMUL.uint32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VMAXS.b16` | — | —/— | — | compatibility |
| `VMAXS.b32` | — | —/— | — | compatibility |
| `VMAXS.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAXS.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.b16` | — | —/— | — | compatibility |
| `VMINS.b32` | — | —/— | — | compatibility |
| `VMINS.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMINS.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.b16` | — | —/— | — | compatibility |
| `VMAX.b32` | — | —/— | — | compatibility |
| `VMAX.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMAX.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.b16` | — | —/— | — | compatibility |
| `VMIN.b32` | — | —/— | — | compatibility |
| `VMIN.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMIN.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMAX.b16` | — | —/— | — | compatibility |
| `VCMAX.b32` | — | —/— | — | compatibility |
| `VCMAX.fp16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMAX.fp32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMAX.int16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMAX.int32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMAX.uint16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMAX.uint32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMIN.b16` | — | —/— | — | compatibility |
| `VCMIN.b32` | — | —/— | — | compatibility |
| `VCMIN.fp16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMIN.fp32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMIN.int16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMIN.int32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMIN.uint16` | 17 | SFU/EXU0_ONLY | 2 | typed_or_physical |
| `VCMIN.uint32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.b16` | — | —/— | — | compatibility |
| `VCADD.b32` | — | —/— | — | compatibility |
| `VCADD.fp16` | 24 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.fp32` | 22 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.int16` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.int32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.uint16` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VCADD.uint32` | 16 | SFU/EXU0_ONLY | 1 | typed_or_physical |
| `VLDS.b16` | — | LSU | 不适用 | compatibility |
| `VLDS.b32` | — | LSU | 不适用 | compatibility |
| `VLDS.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.uint64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDS.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.b16` | — | LSU | 不适用 | compatibility |
| `VSTS.b32` | — | LSU | 不适用 | compatibility |
| `VSTS.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.uint64` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VDUP.b16` | — | —/— | — | compatibility |
| `VDUP.b32` | 11 | ALU/EXU0_ONLY | 1 | compatibility |
| `VDUP.bf16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.f8e4m3` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.f8e5m2` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.f8e8m0` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.fp16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.fp32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.int16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.int32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.int8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.uint16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.uint32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP.uint8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VAND.b16` | — | —/— | — | compatibility |
| `VAND.b32` | — | —/— | — | compatibility |
| `VAND.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VAND.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCGMAX.b16` | — | —/— | — | compatibility |
| `VCGMAX.b32` | — | —/— | — | compatibility |
| `VCGMAX.fp16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMAX.fp32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGMAX.int16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMAX.int32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGMAX.uint16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMAX.uint32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCMP_EQ.b16` | — | —/— | — | compatibility |
| `VCMP_EQ.b32` | — | —/— | — | compatibility |
| `VCMP_EQ.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCMP_EQ.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCVT_F16_TO_F32.f16_to_f32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCVT_F32_TO_F16.f32_to_f16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCVT_F32_TO_BF16.f32_to_bf16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCVT_F32_TO_S32.f32_to_s32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S32_TO_F32.s32_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VSEL.b16` | — | —/— | — | compatibility |
| `VSEL.b32` | — | —/— | — | compatibility |
| `VSEL.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSEL.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.b16` | — | —/— | — | compatibility |
| `VSHLS.b32` | — | —/— | — | compatibility |
| `VSHLS.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHLS.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.b16` | — | —/— | — | compatibility |
| `VSHRS.b32` | — | —/— | — | compatibility |
| `VSHRS.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VSHRS.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.b16` | — | —/— | — | compatibility |
| `VBR.b32` | — | —/— | — | compatibility |
| `VBR.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VBR.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VPACK.b32` | 11 | ALU/EXU0_ONLY | 1 | compatibility |
| `VPACK.s16_to_u8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VPACK.s32_to_u16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VPACK.u16_to_u8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VPACK.u32_to_u16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VSSTB.b16` | 9 | LSU | 不适用 | compatibility |
| `VSSTB.bf16` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.f8e4m3` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.f8e5m2` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.f8e8m0` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.fp16` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.fp32` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.int16` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.int32` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.int8` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.uint16` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.uint32` | 10 | LSU | 不适用 | typed_or_physical |
| `VSSTB.uint8` | 10 | LSU | 不适用 | typed_or_physical |
| `VEXPDIF.b32` | — | —/— | — | compatibility |
| `VEXPDIF.fp16` | 19 | SFU/EXU01 | 4 | typed_or_physical |
| `VEXPDIF.fp32` | 18 | SFU/EXU01 | 4 | typed_or_physical |
| `VMULSCVT.f32_to_f16` | 8 | ALU/EXU01 | 1 | typed_or_physical |
| `VSTUS.b16` | — | LSU | 不适用 | compatibility |
| `VSTUS.b32` | — | LSU | 不适用 | compatibility |
| `VSTUS.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTAS.b16` | — | LSU | 不适用 | compatibility |
| `VSTAS.b32` | — | LSU | 不适用 | compatibility |
| `VSTAS.bf16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.f4e1m2x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.f4e2m1x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.f8e4m3` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.f8e5m2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.f8e8m0` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.fp16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.fp32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.int16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.int32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.int64` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.int8` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.uint16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.uint32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAS.uint8` | 8 | LSU | 不适用 | typed_or_physical |
| `VSQRT.fp16` | 22 | SFU/EXU01 | 8 | typed_or_physical |
| `VSQRT.fp32` | 17 | SFU/EXU01 | 4 | typed_or_physical |
| `VLN.fp16` | 23 | SFU/EXU01 | 8 | typed_or_physical |
| `VLN.fp32` | 18 | SFU/EXU01 | 4 | typed_or_physical |
| `VOR.b32` | 6 | ALU/EXU01 | — | compatibility |
| `VOR.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VOR.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VCI.fp16` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCI.fp32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCI.int16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCI.int32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCI.int8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_BF16_TO_F32.bf16_to_f32` | 7 | ALU/EXU01 | 1 | typed_or_physical |
| `VCVT_S32_TO_U8.s32_to_u8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCGADD.fp16` | 18 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGADD.fp32` | 16 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGADD.int16` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGADD.int32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGADD.uint16` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGADD.uint32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGMIN.fp16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMIN.fp32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGMIN.int16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMIN.int32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCGMIN.uint16` | 14 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `VCGMIN.uint32` | 13 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCPADD.fp16` | 12 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCPADD.fp32` | 12 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCVT_BF16_TO_F16.bf16_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_BF16_TO_S32.bf16_to_s32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_BF16.f16_to_bf16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_S16.f16_to_s16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_S32.f16_to_s32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_S8.f16_to_s8` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_U8.f16_to_u8` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F32_TO_S16.f32_to_s16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_F16.s16_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_F32.s16_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_S32.s16_to_s32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_U32.s16_to_u32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_U8.s16_to_u8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S32_TO_S16.s32_to_s16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S32_TO_U16.s32_to_u16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S8_TO_F16.s8_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S8_TO_S16.s8_to_s16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S8_TO_S32.s8_to_s32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U16_TO_U32.u16_to_u32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U16_TO_U8.u16_to_u8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U32_TO_S16.u32_to_s16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U32_TO_U16.u32_to_u16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U32_TO_U8.u32_to_u8` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U8_TO_F16.u8_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U8_TO_U16.u8_to_u16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_U8_TO_U32.u8_to_u32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VUNPACK.s16_to_s32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VUNPACK.s8_to_s16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VUNPACK.u16_to_u32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VUNPACK.u8_to_u16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VCVT_F32_TO_S64.f32_to_s64` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S32_TO_S64.s32_to_s64` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S64_TO_F32.s64_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S64_TO_S32.s64_to_s32` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_BF16_TO_F4E1M2X2.bf16_to_f4e1m2x2` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_BF16_TO_F4E2M1X2.bf16_to_f4e2m1x2` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_BF16_TO_F8E4M3.bf16_to_f8e4m3` | — | —/— | — | typed_or_physical |
| `VCVT_BF16_TO_F8E5M2.bf16_to_f8e5m2` | — | —/— | — | typed_or_physical |
| `VCVT_F16_TO_F8E4M3.f16_to_f8e4m3` | — | —/— | — | typed_or_physical |
| `VCVT_F16_TO_F8E5M2.f16_to_f8e5m2` | — | —/— | — | typed_or_physical |
| `VCVT_F16_TO_HIF8.f16_to_hif8` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F16_TO_S4X2.f16_to_s4x2` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F32_TO_F8E4M3.f32_to_f8e4m3` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F32_TO_F8E5M2.f32_to_f8e5m2` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F32_TO_HIF8.f32_to_hif8` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F4E1M2X2_TO_BF16.f4e1m2x2_to_bf16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F4E2M1X2_TO_BF16.f4e2m1x2_to_bf16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F8E4M3_TO_F32.f8e4m3_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_F8E5M2_TO_F32.f8e5m2_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_HIF8_TO_F16.hif8_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_HIF8_TO_F32.hif8_to_f32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S16_TO_S4X2.s16_to_s4x2` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S4X2_TO_BF16.s4x2_to_bf16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S4X2_TO_F16.s4x2_to_f16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VCVT_S4X2_TO_S16.s4x2_to_s16` | 7 | ALU/EXU01 | 2 | typed_or_physical |
| `VMOV_MERGE.b32` | — | —/— | — | compatibility |
| `VMOV_MERGE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VMOV_MERGE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `PINTLV.b16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PINTLV.b32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PINTLV.b8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PDINTLV.b16` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PDINTLV.b32` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PDINTLV.b8` | 11 | ALU/EXU0_ONLY | 2 | typed_or_physical |
| `PSLIDE.b16` | — | —/— | — | typed_or_physical |
| `PSLIDE.b32` | — | —/— | — | typed_or_physical |
| `PSLIDE.b8` | — | —/— | — | typed_or_physical |
| `PLTM.b16` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PLTM.b32` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PLTM.b8` | 6 | ALU/EXU01 | 2 | typed_or_physical |
| `PPACK.b8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `PUNPACK.b8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VSELR.bf16` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.f8e4m3` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.f8e5m2` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.f8e8m0` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.fp16` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.int16` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.int32` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.int8` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.uint16` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.uint32` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELR.uint8` | 16 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VSELRV2.bf16` | — | —/— | — | typed_or_physical |
| `VSELRV2.fp16` | — | —/— | — | typed_or_physical |
| `VSELRV2.int16` | — | —/— | — | typed_or_physical |
| `VSELRV2.uint16` | — | —/— | — | typed_or_physical |
| `VSQZ.bf16` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.f8e4m3` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.f8e5m2` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.f8e8m0` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.fp16` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.fp32` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.hif8` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.int16` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.int32` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.int8` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.uint16` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.uint32` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VSQZ.uint8` | 18 | ALU/EXU0_ONLY | 5 | typed_or_physical |
| `VUSQZ.int16` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VUSQZ.int32` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VUSQZ.int8` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VUSQZ.uint16` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VUSQZ.uint32` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VUSQZ.uint8` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VTRC.bf16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VTRC.fp16` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `VTRC.fp32` | 8 | ALU/EXU01 | 2 | typed_or_physical |
| `DHISTV2.uint16` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `CHISTV2.uint16` | 15 | ALU/EXU0_ONLY | 4 | typed_or_physical |
| `VLDUI.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUI.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.b16` | — | LSU | 不适用 | compatibility |
| `VSTUI.b32` | — | LSU | 不适用 | compatibility |
| `VSTUI.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUI.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTAI.b16` | — | LSU | 不适用 | compatibility |
| `VSTAI.b32` | — | LSU | 不适用 | compatibility |
| `VSTAI.bf16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.f4e1m2x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.f4e2m1x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.f8e4m3` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.f8e5m2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.f8e8m0` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.fp16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.fp32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.int16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.int32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.int64` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.int8` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.uint16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.uint32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTAI.uint8` | 8 | LSU | 不适用 | typed_or_physical |
| `PLD.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `PLDI.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `PST.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `PSTI.b8` | 9 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.bf16` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.f8e4m3` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.f8e5m2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.f8e8m0` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.fp16` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.fp32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.int16` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.int32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.int8` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.uint16` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.uint32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2.uint8` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.bf16` | 20 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.fp16` | 20 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.fp32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.int16` | 20 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.int32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.uint16` | 20 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BC.uint32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.bf16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.f4e1m2x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.f4e2m1x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.f8e4m3` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.f8e5m2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.f8e8m0` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.fp16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.fp32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.int16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.int32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.int64` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.int8` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.uint16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.uint32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.uint64` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB.uint8` | 21 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.bf16` | 31 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.f8e4m3` | 21 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.f8e5m2` | 21 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.f8e8m0` | 21 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.fp16` | 31 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.fp32` | 15 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.int16` | 31 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.int32` | 15 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.int8` | 21 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.uint16` | 31 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.uint32` | 15 | LSU | 不适用 | typed_or_physical |
| `VSCATTER.uint8` | 21 | LSU | 不适用 | typed_or_physical |
| `VDUP_MERGE.bf16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.f8e4m3` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.f8e5m2` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.f8e8m0` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.fp16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.fp32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.int16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.int32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.int8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.uint16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.uint32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_MERGE.uint8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.bf16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.f8e4m3` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.f8e5m2` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.f8e8m0` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.fp16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.fp32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.int16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.int32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.int8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.uint16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.uint32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST.uint8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.bf16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.f8e4m3` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.f8e5m2` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.f8e8m0` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.fp16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.fp32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.int16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.int32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.int8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.uint16` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.uint32` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VDUP_HIGHEST_MERGE.uint8` | 11 | ALU/EXU0_ONLY | 1 | typed_or_physical |
| `VLDS_PAIR.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTS_BYTES.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VDUPS.b16` | — | —/— | — | compatibility |
| `VDUPS.b32` | — | —/— | — | compatibility |
| `VDUPS.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.bf16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.f8e4m3` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.f8e5m2` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.f8e8m0` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.fp16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.fp32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.int32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.uint32` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPS_MERGE.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VLD.b16` | — | LSU | 不适用 | compatibility |
| `VLD.b32` | — | LSU | 不适用 | compatibility |
| `VLD.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.s4x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.uint64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLD.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.b16` | — | LSU | 不适用 | compatibility |
| `VST.b32` | — | LSU | 不适用 | compatibility |
| `VST.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.s4x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.uint64` | 9 | LSU | 不适用 | typed_or_physical |
| `VST.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDA.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSLDB.bf16` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.f4e1m2x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.f4e2m1x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.f8e4m3` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.f8e5m2` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.f8e8m0` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.fp16` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.fp32` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.int16` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.int32` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.int64` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.int8` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.uint16` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.uint32` | 21 | LSU | 不适用 | typed_or_physical |
| `VSLDB.uint8` | 21 | LSU | 不适用 | typed_or_physical |
| `VSST.bf16` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.fp16` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.fp32` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.int16` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.int32` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.int8` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.uint16` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.uint32` | 27 | LSU | 不适用 | typed_or_physical |
| `VSST.uint8` | 27 | LSU | 不适用 | typed_or_physical |
| `VSTUR.b16` | — | LSU | 不适用 | compatibility |
| `VSTUR.b32` | — | LSU | 不适用 | compatibility |
| `VSTUR.bf16` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.f4e1m2x2` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.f4e2m1x2` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.f8e4m3` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.f8e5m2` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.f8e8m0` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.fp16` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.fp32` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.int16` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.int32` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.int64` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.int8` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.uint16` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.uint32` | — | LSU | 不适用 | typed_or_physical |
| `VSTUR.uint8` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.b16` | — | LSU | 不适用 | compatibility |
| `VSTAR.b32` | — | LSU | 不适用 | compatibility |
| `VSTAR.bf16` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.f4e1m2x2` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.f4e2m1x2` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.f8e4m3` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.f8e5m2` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.f8e8m0` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.fp16` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.fp32` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.int16` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.int32` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.int64` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.int8` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.uint16` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.uint32` | — | LSU | 不适用 | typed_or_physical |
| `VSTAR.uint8` | — | LSU | 不适用 | typed_or_physical |
| `VLDU.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDU.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.b16` | — | LSU | 不适用 | compatibility |
| `VSTU.b32` | — | LSU | 不适用 | compatibility |
| `VSTU.bf16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.f4e1m2x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.f4e2m1x2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.f8e4m3` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.f8e5m2` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.f8e8m0` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.fp16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.fp32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.int16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.int32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.int64` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.int8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.uint16` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.uint32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTU.uint8` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTA.b16` | — | LSU | 不适用 | compatibility |
| `VSTA.b32` | — | LSU | 不适用 | compatibility |
| `VSTA.bf16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.f4e1m2x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.f4e2m1x2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.f8e4m3` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.f8e5m2` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.f8e8m0` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.fp16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.fp32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.int16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.int32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.int64` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.int8` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.uint16` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.uint32` | 8 | LSU | 不适用 | typed_or_physical |
| `VSTA.uint8` | 8 | LSU | 不适用 | typed_or_physical |
| `VDUPI.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPI.int8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPI.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPI.uint8` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPI_HI.int16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VDUPI_HI.uint16` | 6 | ALU/EXU01 | 1 | typed_or_physical |
| `VLDAS_BYTES.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VLDUS_BYTES.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTUS_BYTES.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VSTAS_BYTES.b32` | 9 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BYTES.int32` | 23 | LSU | 不适用 | typed_or_physical |
| `VGATHER2_BYTES.uint32` | 23 | LSU | 不适用 | typed_or_physical |
| `VSCATTER_BYTES.int32` | 15 | LSU | 不适用 | typed_or_physical |
| `VSCATTER_BYTES.uint32` | 15 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.bf16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.f4e1m2x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.f4e2m1x2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.f8e4m3` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.f8e5m2` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.f8e8m0` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.fp16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.fp32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.int16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.int32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.int64` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.int8` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.uint16` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.uint32` | 21 | LSU | 不适用 | typed_or_physical |
| `VGATHERB_UNMASKED.uint8` | 21 | LSU | 不适用 | typed_or_physical |

## 工具链限制

`VSADD/VSADDS/VSSUB`、`PSLIDE_B8/B16/B32`、`VNOP` 与 `VNOPXN` 的 builtin 被该目标编译器拒绝。
`VNOPXN` 为宏接口，单列在机器清单，未计入函数签名总数。
VSELRV2 和四种 FP16/BF16→FP8 转换缺少 CAModel VALU 条目；VSTUR/VSTAR 的有效状态探针在 CAModel 中超时。
这些项保留语义合同及诊断，未写入伪造的 latency、II 或 forwarding。

## 排除的接口

- `get_repeat_ceiling`：标量配置查询。
- `set_ub_addr_upper_bound`：UB 边界特殊寄存器配置。
- `sprclr`：特殊寄存器控制。
- `sprsti`：特殊寄存器控制。
- `sprsts`：特殊寄存器控制。

## 复现

```bash
python3 tools/validate_a5_cce_signatures.py --out /tmp/a5-signatures
python3 tools/audit_a5_instruction_coverage.py --cann /home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1 --signature-report /tmp/a5-signatures/signatures.json --output-dir docs/instruction_coverage/a5
```

[机器清单](instruction_coverage/a5/coverage.json) 包含所有声明和配置 SHA256。
原始预处理文本放在忽略目录 `audit_artifacts/`。参数获取方法见 [benchmark skill](../skills/vfsim-instruction-benchmark/SKILL.md)。
