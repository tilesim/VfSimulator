> 百分比为绝对相对误差 `abs(VfSim - CAModel) / CAModel × 100%`，越低越好，不是 `1 - error` 的预测精度。历史列保留原值；最新列对应文末的 `bdea3dc` 实测回归结果。

| case | baseline(consumer-done) | start+5 | start+5+queue_level2 | start+5+queue_level3 | queue_level4+vregpass (shq=inf exq=inf) | queue_level4+vregpass (shq=58 exq=26) | queue_level4+ooo-transfer-delay | queue_level4+rr-reserve(min1 cap7) | current canonical+balanced-RR (`bdea3dc`) |
|---|---:|---:|---:|---:|---:|---:|---:|---:| ---: |
| gelu_poly_i16_u1 | 12.33% | 12.33% | 10.76% | 9.19% | 12.11% | 12.11% | 12.33% | 9.42% | 9.42% |
| gelu_poly_i64_u1 | 15.05% | 15.05% | 12.65% | 11.05% | 13.78% | 13.78% | 12.85% | 12.18% | 12.18% |
| gelu_poly_i96_u1 | 17.10% | NA | 14.43% | 12.83% | 15.59% | 15.59% | 14.70% | 14.12% | 14.12% |
| gelu_i16_u1 | 8.02% | 8.02% | 11.76% | 8.02% | 1.07% | 1.07% | 0.53% | 1.07% | 1.07% |
| online_update_i64_u1 | 13.09% | 13.09% | 11.96% | 10.84% | 2.93% | 2.93% | 2.03% | 1.13% | 2.03% |
| probe_src_fanout | 0.70% | 0.70% | 1.05% | 1.05% | 1.05% | 1.05% | 1.05% | 1.05% | 1.05% |
| probe_branch_live_range | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% | 1.36% |
| probe_store_capture_reuse | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% | 0.63% |
| vadds_longchain_i16_1x512 | 0.14% | 0.14% | 0.14% | 0.07% | 0.14% | 1.44% | 1.51% | 1.51% | 1.51% |
| vadds_longchain_i64_8x64 | 2.17% | 2.17% | 2.17% | 1.13% | 2.17% | 6.64% | 7.37% | 6.05% | 6.05% |
| vadds_longchain_i128_128x4 | 1.39% | NA | 1.39% | 1.39% | 1.39% | 1.39% | 1.39% | 10.41% | 10.41% |
| vexp_longchain_i16_1x512 | 1.81% | 1.81% | 1.79% | 1.74% | 1.79% | 0.02% | 0.00% | 0.00% | 0.00% |
| vexp_longchain_i64_8x64 | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| vexp_longchain_i128_128x4 | NA | NA | NA | NA | NA | NA | NA | NA | NA |
| gelu_poly_i96_u2 | 12.52% | 12.52% | 9.50% | 9.50% | 8.41% | 8.41% | 9.40% | 8.16% | 8.16% |
| gelu_poly_i96_u4 | 2.45% | 2.45% | 8.94% | 2.69% | 1.75% | 1.75% | 3.80% | 3.80% | 3.80% |
| gelu_poly_i96_u8 | 0.29% | 0.29% | 6.11% | 30.38% | 1.98% | 1.98% | 0.70% | 0.70% | 0.06% |
| silu_i16_u1 | 6.88% | 6.88% | 10.00% | 6.88% | 2.50% | 2.50% | 3.12% | 3.75% | 3.75% |
| swiglu_i16_u1 | 6.67% | 6.67% | 7.78% | 6.11% | 6.11% | 6.11% | 6.11% | 4.44% | 4.44% |
| silu_i64_u1 | 8.03% | 8.03% | 10.88% | 5.70% | 5.18% | 5.18% | 5.18% | 6.48% | 6.48% |
| silu_i96_u1 | 9.48% | 9.48% | 11.15% | 5.95% | 6.13% | 6.13% | 6.51% | 8.18% | 8.18% |
| swiglu_i64_u1 | 9.38% | 9.38% | 9.58% | 4.79% | 5.21% | 5.21% | 3.54% | 2.29% | 2.29% |
| swiglu_i96_u1 | 9.44% | 9.44% | 9.14% | 4.42% | 3.83% | 3.83% | 3.39% | 2.51% | 2.51% |

## Camodel and queue_level4+vregpass(shq=58 exq=26) Time

| case | loop block count | loop iteration count | per-loop chain length | camodel VF end | queue_level4+vregpass (shq=58 exq=26) VF end |
|---|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 1 | 16 | NA | 446 | 392 |
| gelu_poly_i64_u1 | 1 | 64 | NA | 1502 | 1295 |
| gelu_poly_i96_u1 | 1 | 96 | NA | 2245 | 1895 |
| gelu_i16_u1 | 1 | 16 | NA | 187 | 189 |
| online_update_i64_u1 | 1 | 64 | NA | 443 | 430 |
| probe_src_fanout | NA | NA | NA | 285 | 282 |
| probe_branch_live_range | NA | NA | NA | 221 | 218 |
| probe_store_capture_reuse | NA | NA | NA | 319 | 317 |
| vadds_longchain_i16_1x512 | 1 | 16 | 512 | 21823 | 22138 |
| vadds_longchain_i64_8x64 | 8 | 64 | 64 | 37281 | 39755 |
| vadds_longchain_i128_128x4 | 128 | 128 | 4 | 36641 | 36131 |
| vexp_longchain_i16_1x512 | 1 | 16 | 512 | 95492 | 95477 |
| vexp_longchain_i64_8x64 | 8 | 64 | 64 | NA | 163947 |
| vexp_longchain_i128_128x4 | 128 | 128 | 4 | NA | 134947 |
| gelu_poly_i96_u2 | 1 | 96 | NA | 2021 | 1851 |
| gelu_poly_i96_u4 | 1 | 96 | NA | 1712 | 1682 |
| gelu_poly_i96_u8 | 1 | 96 | NA | 1718 | 1684 |
| silu_i16_u1 | 1 | 16 | NA | 160 | 156 |
| swiglu_i16_u1 | 1 | 16 | NA | 180 | 169 |
| silu_i64_u1 | 1 | 64 | NA | 386 | 406 |
| silu_i96_u1 | 1 | 96 | NA | 538 | 571 |
| swiglu_i64_u1 | 1 | 64 | NA | 480 | 455 |
| swiglu_i96_u1 | 1 | 96 | NA | 678 | 652 |

## Camodel and queue_level4+ooo-transfer-delay Time

| case | loop block count | loop iteration count | per-loop chain length | camodel VF end | queue_level4+ooo-transfer-delay VF end |
|---|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 1 | 16 | NA | 446 | 391 |
| gelu_poly_i64_u1 | 1 | 64 | NA | 1502 | 1309 |
| gelu_poly_i96_u1 | 1 | 96 | NA | 2245 | 1915 |
| gelu_i16_u1 | 1 | 16 | NA | 187 | 188 |
| online_update_i64_u1 | 1 | 64 | NA | 443 | 434 |
| probe_src_fanout | NA | NA | NA | 285 | 282 |
| probe_branch_live_range | NA | NA | NA | 221 | 218 |
| probe_store_capture_reuse | NA | NA | NA | 319 | 317 |
| vadds_longchain_i16_1x512 | 1 | 16 | 512 | 21823 | 22153 |
| vadds_longchain_i64_8x64 | 8 | 64 | 64 | 37281 | 40027 |
| vadds_longchain_i128_128x4 | 128 | 128 | 4 | 36641 | 36131 |
| vexp_longchain_i16_1x512 | 1 | 16 | 512 | 95492 | 95492 |
| vexp_longchain_i64_8x64 | 8 | 64 | 64 | NA | 163963 |
| vexp_longchain_i128_128x4 | 128 | 128 | 4 | NA | 134947 |
| gelu_poly_i96_u2 | 1 | 96 | NA | 2021 | 1831 |
| gelu_poly_i96_u4 | 1 | 96 | NA | 1712 | 1777 |
| gelu_poly_i96_u8 | 1 | 96 | NA | 1718 | 1706 |
| silu_i16_u1 | 1 | 16 | NA | 160 | 155 |
| swiglu_i16_u1 | 1 | 16 | NA | 180 | 169 |
| silu_i64_u1 | 1 | 64 | NA | 386 | 406 |
| silu_i96_u1 | 1 | 96 | NA | 538 | 573 |
| swiglu_i64_u1 | 1 | 64 | NA | 480 | 463 |
| swiglu_i96_u1 | 1 | 96 | NA | 678 | 655 |

## Camodel and queue_level4+rr-reserve(min1 cap7) Time

| case | loop block count | loop iteration count | per-loop chain length | camodel VF end | queue_level4+rr-reserve(min1 cap7) VF end |
|---|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 1 | 16 | NA | 446 | 404 |
| gelu_poly_i64_u1 | 1 | 64 | NA | 1502 | 1319 |
| gelu_poly_i96_u1 | 1 | 96 | NA | 2245 | 1928 |
| gelu_i16_u1 | 1 | 16 | NA | 187 | 189 |
| online_update_i64_u1 | 1 | 64 | NA | 443 | 438 |
| probe_src_fanout | NA | NA | NA | 285 | 282 |
| probe_branch_live_range | NA | NA | NA | 221 | 218 |
| probe_store_capture_reuse | NA | NA | NA | 319 | 317 |
| vadds_longchain_i16_1x512 | 1 | 16 | 512 | 21823 | 22153 |
| vadds_longchain_i64_8x64 | 8 | 64 | 64 | 37281 | 39536 |
| vadds_longchain_i128_128x4 | 128 | 128 | 4 | 36641 | 32827 |
| vexp_longchain_i16_1x512 | 1 | 16 | 512 | 95492 | 95492 |
| vexp_longchain_i64_8x64 | 8 | 64 | 64 | NA | 161198 |
| vexp_longchain_i128_128x4 | 128 | 128 | 4 | NA | 131137 |
| gelu_poly_i96_u2 | 1 | 96 | NA | 2021 | 1856 |
| gelu_poly_i96_u4 | 1 | 96 | NA | 1712 | 1777 |
| gelu_poly_i96_u8 | 1 | 96 | NA | 1718 | 1706 |
| silu_i16_u1 | 1 | 16 | NA | 160 | 154 |
| swiglu_i16_u1 | 1 | 16 | NA | 180 | 172 |
| silu_i64_u1 | 1 | 64 | NA | 386 | 411 |
| silu_i96_u1 | 1 | 96 | NA | 538 | 582 |
| swiglu_i64_u1 | 1 | 64 | NA | 480 | 469 |
| swiglu_i96_u1 | 1 | 96 | NA | 678 | 661 |

## Camodel and Current Canonical+Balanced-RR Time (bdea3dc)

- 更新日期：2026-09-28；分支：`vfinfo-core-api-unification`；预测代码 commit：`bdea3dcc01b3be0a0c602320e9e6b9508bc7010a`。
- Python 默认 A5 配置：Canonical 执行路径、EXU0 平衡预留 RR（lookahead=8、min_count=1、cap=7）、LSU 两槽/共享 512 B 每周期、store 优先阈值=1、全局 Membar；vector=68，predicate=32。
- 输入：`regression_suite/cases/cost_model_regression_cases.json` 引用的 Canonical fixtures，使用各 case 的参数。回归脚本显式关闭动态指令数量上限，以运行长链用例；未修改基准或指令时序。
- 25 个 case 完整重跑；本表包含历史表未列出的 GeLU_poly U3/U6。CAModel 时间沿用已登记参考值，本次未重新运行 CAModel。
- `NA` 表示缺少可用 CAModel 参考时间，不能计算误差，不代表 VfSim 未完成。旧 fixtures 未补入 CCE 的 PSET/mask 指令，因此本表不是“最新 CCE 重新解析”的预测结果。
- 周期为含尾开销的 `vf_end_cycle`，不是仿真程序的宿主机运行耗时。原始结果：[current_metrics.json](../../results/regression_suite/precision_refresh_bdea3dc/current_metrics.json)，[回归检查](../../results/regression_suite/precision_refresh_bdea3dc/compare_summary.json)。

复现命令：

```bash
python3 tools/run_cost_model_regression.py --tier full --out-dir results/regression_suite/precision_refresh_bdea3dc
```

| case | loop block count | loop iteration count | per-loop chain length | camodel VF end | current canonical+balanced-RR (bdea3dc) VF end |
|---|---:|---:|---:|---:|---:|
| gelu_poly_i16_u1 | 1 | 16 | NA | 446 | 404 |
| gelu_poly_i64_u1 | 1 | 64 | NA | 1502 | 1319 |
| gelu_poly_i96_u1 | 1 | 96 | NA | 2245 | 1928 |
| gelu_i16_u1 | 1 | 16 | NA | 187 | 189 |
| online_update_i64_u1 | 1 | 64 | NA | 443 | 434 |
| probe_src_fanout | NA | NA | NA | 285 | 282 |
| probe_branch_live_range | NA | NA | NA | 221 | 218 |
| probe_store_capture_reuse | NA | NA | NA | 319 | 317 |
| vadds_longchain_i16_1x512 | 1 | 16 | 512 | 21823 | 22153 |
| vadds_longchain_i64_8x64 | 8 | 64 | 64 | 37281 | 39536 |
| vadds_longchain_i128_128x4 | 128 | 128 | 4 | 36641 | 32827 |
| vexp_longchain_i16_1x512 | 1 | 16 | 512 | 95492 | 95492 |
| vexp_longchain_i64_8x64 | 8 | 64 | 64 | NA | 161198 |
| vexp_longchain_i128_128x4 | 128 | 128 | 4 | NA | 131137 |
| gelu_poly_i96_u2 | 1 | 96 | NA | 2021 | 1856 |
| gelu_poly_i96_u3 | 1 | 96 | NA | NA | 1807 |
| gelu_poly_i96_u4 | 1 | 96 | NA | 1712 | 1777 |
| gelu_poly_i96_u6 | 1 | 96 | NA | NA | 1746 |
| gelu_poly_i96_u8 | 1 | 96 | NA | 1718 | 1717 |
| silu_i16_u1 | 1 | 16 | NA | 160 | 154 |
| swiglu_i16_u1 | 1 | 16 | NA | 180 | 172 |
| silu_i64_u1 | 1 | 64 | NA | 386 | 411 |
| silu_i96_u1 | 1 | 96 | NA | 538 | 582 |
| swiglu_i64_u1 | 1 | 64 | NA | 480 | 469 |
| swiglu_i96_u1 | 1 | 96 | NA | 678 | 661 |

## Softmax CCE 当前预测精度 (bdea3dc)

- 更新日期：2026-09-28；分支：`vfinfo-core-api-unification`；代码 commit：`bdea3dcc01b3be0a0c602320e9e6b9508bc7010a`。
- 策略沿用上述当前 A5 默认配置：Canonical + 平衡预留 RR（lookahead=8、min_count=1、cap=7），全局 Membar。
- 范围为 [cce_code/softmax](../../cce_code/softmax) 中实际存在的四组 U1/U2/U4，共 12 个 CCE；本表不包含该目录 README 中提及但当前目录未收录的 ABCABC 用例。
- VfSim 由当前 CCE 前端重新解析后预测，包含当前支持的 PSET/predicate 与 POST_UPDATE 语义，不复用历史 README 中的预测值。
- CAModel 周期取自各 CCE 同目录的 `core0.veccore0.instr_log.dump` 中的 `vf_execute_time`。本次未重跑 CAModel，也未重新执行数值正确性校验。
- 时间单位为 cycle，VfSim 使用含尾开销的 VF end；误差 = VfSim - CAModel；预测精度 = `(1 - abs(VfSim - CAModel) / CAModel) * 100%`，不是算子数值精度。
- 原始汇总与逐项源码/参考日志路径：[current_metrics.json](../../results/regression_suite/softmax_precision_refresh_bdea3dc/current_metrics.json)。各组的 `u1/u2/u4` 输出目录保留本次 VfSim 日志。

| 指令组合 | Unroll | CAModel (cycles) | VfSim (cycles) | 误差 (cycles) | 预测精度 |
|---|---:|---:|---:|---:|---:|
| vexpdif + vmulscvt | U1 | 713 | 659 | -54 | 92.43% |
| vexpdif + vmulscvt | U2 | 609 | 626 | +17 | 97.21% |
| vexpdif + vmulscvt | U4 | 594 | 628 | +34 | 94.28% |
| vexpdif + vcvt | U1 | 615 | 641 | +26 | 95.77% |
| vexpdif + vcvt | U2 | 622 | 614 | -8 | 98.71% |
| vexpdif + vcvt | U4 | 618 | 620 | +2 | 99.68% |
| vsub + vexp + vmulscvt | U1 | 701 | 701 | 0 | 100.00% |
| vsub + vexp + vmulscvt | U2 | 668 | 682 | +14 | 97.90% |
| vsub + vexp + vmulscvt | U4 | 660 | 685 | +25 | 96.21% |
| vsub + vexp + vcvt | U1 | 686 | 681 | -5 | 99.27% |
| vsub + vexp + vcvt | U2 | 681 | 670 | -11 | 98.38% |
| vsub + vexp + vcvt | U4 | 682 | 678 | -4 | 99.41% |

12 个 case 的平均预测精度为 **97.44%**，平均绝对相对误差为 **2.56%**，均为逐 case 等权算术平均。

单个 case 复现示例（在仓库根目录运行）：

```bash
python3 main.py --cce cce_code/softmax/expdif_mulcvt/u1_misched0/fa_softmax_macro_instr_ir_layout.cce --out_dir results/regression_suite/softmax_precision_refresh_bdea3dc/expdif_mulcvt/u1
```
