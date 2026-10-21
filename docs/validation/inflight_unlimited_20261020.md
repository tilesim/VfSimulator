# EXU inflight 无上限对照

每 EXU 每周期一条完成约束的四组对照见
[EXU 完成带宽验证](exu_completion_bandwidth_20261020.md)。

验证日期为 2026-10-20，基于 `dev_wch/refactor` 的 II 发射规则修正。
实验通过 Canonical 输入中的 `uarch.exq_issue_inflight_cap_per_port=0` 关闭每个 EXU
已发射、尚未完成的计算指令数量上限。基准值为 7，配置中的 0 表示无限制。

## 方法

固定上一轮完整回归的 25 个 workload、循环参数、latency、forwarding、II 及其他
微架构参数。每个实验输入与对应基准输入的唯一差异为上述 inflight 字段。
基础配置取 `configs/uarch.json` 的 7，实验使用输入覆盖 0。

Python 使用 `CoreVfCostModel(out_dir=None)`，C++ runner 使用空输出目录，
两者运行完整仿真并保存周期指标。Python 对 EXU 发射做只读观测，记录实际最大
inflight 和发射前的 self-II 截止周期。所有结果采用 VF drain 后的结束周期。

## 结果

25 个案例均完成仿真，Python/C++ 的结束周期、执行周期和静态指令数全部一致。
13 个案例的预测周期降低，12 个不变。21 个具有 CAModel 参考值的案例中，
2 个误差减小、9 个误差增大、10 个不变；4 个无参考值的案例只比较周期和双实现结果。

| 模型设置 | MAPE | WAPE | 相对绝对误差中位数 |
| --- | ---: | ---: | ---: |
| II 修正前，inflight=7 | 4.738% | 3.566% | 3.750% |
| II 修正后，inflight=7 | 5.199% | 3.645% | 3.687% |
| II 修正后，inflight=0 | 6.903% | 3.749% | 7.249% |

MAPE 为 21 个案例相对绝对误差的等权平均；WAPE 为总绝对周期误差除以总参考周期。
关闭上限后的 MAPE 相对 II 修正且 inflight=7 的基准增加约 1.70 个百分点。

运行中单端口最大 inflight 为 11，self-II 违规为 0。
`gelu_i16_u1` 的两个端口峰值分别为 10、11；`silu_i64_u1` 为 10、10。
部分 workload 即使出现超过 7 的瞬时 inflight，总周期也保持相同。

## 逐例结果

误差为 `abs(prediction-reference)/reference`，峰值列依次为 EXU0、EXU1。

| workload | 参考周期 | inflight=7 | inflight=0 | 原误差 | 对照误差 | 对照峰值 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| gelu_poly_i16_u1 | 446 | 404 | 404 | 9.42% | 9.42% | 7、7 |
| gelu_poly_i64_u1 | 1502 | 1319 | 1319 | 12.18% | 12.18% | 8、8 |
| gelu_poly_i96_u1 | 2245 | 1928 | 1928 | 14.12% | 14.12% | 8、8 |
| gelu_i16_u1 | 187 | 188 | 171 | 0.53% | 8.56% | 10、11 |
| online_update_i64_u1 | 443 | 437 | 402 | 1.35% | 9.26% | 9、9 |
| probe_src_fanout | 285 | 282 | 282 | 1.05% | 1.05% | 7、7 |
| probe_branch_live_range | 221 | 218 | 218 | 1.36% | 1.36% | 7、7 |
| probe_store_capture_reuse | 319 | 317 | 317 | 0.63% | 0.63% | 7、7 |
| vadds_longchain_i16_1x512 | 21823 | 22153 | 22153 | 1.51% | 1.51% | 3、3 |
| vadds_longchain_i64_8x64 | 37281 | 39536 | 39536 | 6.05% | 6.05% | 4、4 |
| vadds_longchain_i128_128x4 | 36641 | 32827 | 32827 | 10.41% | 10.41% | 7、7 |
| vexp_longchain_i16_1x512 | 95492 | 95492 | 95492 | 0.00% | 0.00% | 2、2 |
| vexp_longchain_i64_8x64 | — | 161198 | 161198 | — | — | 3、3 |
| vexp_longchain_i128_128x4 | — | 131137 | 131137 | — | — | 4、4 |
| gelu_poly_i96_u2 | 2021 | 1856 | 1784 | 8.16% | 11.73% | 8、8 |
| gelu_poly_i96_u3 | — | 1794 | 1742 | — | — | 9、9 |
| gelu_poly_i96_u4 | 1712 | 1908 | 1886 | 11.45% | 10.16% | 8、8 |
| gelu_poly_i96_u6 | — | 1746 | 1671 | — | — | 9、9 |
| gelu_poly_i96_u8 | 1718 | 1695 | 1638 | 1.34% | 4.66% | 10、10 |
| silu_i16_u1 | 160 | 156 | 149 | 2.50% | 6.88% | 10、10 |
| swiglu_i16_u1 | 180 | 170 | 167 | 5.56% | 7.22% | 9、9 |
| silu_i64_u1 | 386 | 412 | 359 | 6.74% | 6.99% | 10、10 |
| silu_i96_u1 | 538 | 580 | 499 | 7.81% | 7.25% | 10、10 |
| swiglu_i64_u1 | 480 | 464 | 443 | 3.33% | 7.71% | 10、9 |
| swiglu_i96_u1 | 678 | 653 | 625 | 3.69% | 7.82% | 10、9 |

误差减小的两个案例是 `gelu_poly_i96_u4` 和 `silu_i96_u1`。
例如 `gelu_poly_i96_u4` 从 1908 降至 1886 cycle，参考为 1712，
误差从 11.45% 降至 10.16%；`gelu_i16_u1` 从 188 降至 171，
参考为 187，误差从 0.53% 增至 8.56%。

按原有回归容差比较，3 项周期检查、7 项精度退化检查未通过；
两种检查涉及 9 个不同案例，详情见机器报告。

## 解释范围

本组数据表明，inflight 上限对当前混合流的重叠执行和总周期有明显影响。
仅保留当前 II 与其他既有约束时，完全关闭该上限会增大这组 workload 的平均误差。
7 的硬件依据及其与内部计算、读写端口等资源的关系需要独立实验确认。
本对照用于评估模型敏感性，实验结果支持在当前参数组合下保留默认值 7。

## 证据与复现

[机器报告](../../results/inflight_unlimited_20261020/analysis.json) 保存全部案例、
三组汇总指标、峰值 inflight、双实现对照、固定参数 SHA256 和失败检查。
同目录的 `inputs.json` 记录 25 个覆盖输入的 SHA256，`inputs/` 保存实际 Canonical 输入，
`python_metrics.json`、`native_metrics.json` 保存两种实现的指标。

实验目录提供 `run_experiment.py`，通过 InputAPI 验证输入并运行仿真。
在仓库根目录执行，`--runner` 指向包含 II 修正的 C++ runner：

```bash
python3 results/inflight_unlimited_20261020/run_experiment.py --backend python
python3 results/inflight_unlimited_20261020/run_experiment.py --backend native --runner /path/to/vfsim_native_json_runner
```

基准周期与 II 规则验证见 [II 发射规则回归](issue_interval_regression_20261020.md)。
