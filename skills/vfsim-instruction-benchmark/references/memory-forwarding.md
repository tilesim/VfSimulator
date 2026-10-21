# A5 VLD→VST uint16 转发

目标为 CANN `9.0.0-beta.1`、`Ascend950PR_9599`、`dav-c310-vec`、
`__NPU_ARCH__=3101`。测量日期为 2026-10-21。

## 参数与适用范围

`forwarding.json` 的精确配置为：

```json
"VLD.uint16": {
  "VST.uint16": 8
}
```

该实测值对应 `vld(...,NORM)`→`vst(...,NORM_B16,PAT_ALL)`，消费者从 Vd
读取 LOAD 的结果。VAG 在保留的四次硬件循环中产生 256 字节步幅。
LOAD/STORE 自身 latency 均为 9；转发间隔单独记录为 8 cycle。
其他 dtype、分布和 mask 的转发间隔需要各自的测量证据。

## 样本与隔离

四组不同随机输入分别运行基础探针和预热探针，共 8 个数值案例、32 组 RAW，
全部通过输出、未写区域和尾部保护区的逐字节校验。输出初值与目标输入来自不同数据区，
每条 STORE 结果均可观察。预热链的结果写入独立输出区并参与校验。

预热探针使谓词提前就绪。4 个可录入样本满足以下条件：

- IDU/ISU 中 STORE 源 Vd 与 LOAD 目的 Vd 的物理寄存器身份一致。
- 其他向量源和谓词在 LOAD 发射前完成。
- LOAD popped 与 `LSU_I1 - 1` 一致，STORE popped 与 `STU_IB_BUF.RECV - 1` 一致。
- STORE 提前分发，在 ISU 的 `src_rdy` 周期立即发射。

4 个有效样本的 forwarding 均为 8。全部 32 组的 LSU 流水线起点间隔也均为 8。
循环后续 LOAD 存在屏障前排队，popped 差值不参与参数提取。
逐动态 ID、输入/输出与日志哈希见 [证据](evidence/a5-vld-vst-uint16.json)。

## 复现与验证

在仓库根目录执行，运行目录须为尚未存在的目录：

```bash
python3 skills/vfsim-instruction-benchmark/scripts/memory_address.py \
  --out "$PWD/results/vld_vst_uint16_probe" \
  --operations vld_vst --dtypes uint16 --replicas 4
```

原始源码、输入、golden、编译命令、数值校验、ISU/LSU 和指令日志保存在
`results/vld_vst_uint16_20261021/`，压缩归档为其中的 `raw_runs.tar.gz`。
该目录内的 `build_evidence.py` 提取满足隔离条件的样本。

`VFtest/cce/contracts/vector_a5_address.cce` 的 Python/C++ 指令发射、完成轨迹及周期一致，
预测为 63 cycle。该例子的 `missing_forwarding_pair` 已消除；
`VV_ALL` 和 `VST_VST` 的 `unmeasured_membar_timing` 继续保留。
