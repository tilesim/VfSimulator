# II 发射规则与精度回归

每 EXU inflight 上限关闭后的敏感性分析见
[EXU inflight 无上限对照](inflight_unlimited_20261020.md)。

验证日期为 2026-10-20，工作分支为 `dev_wch/refactor`。
参数与输入取自本轮开始时的工作区快照。Python 与 C++ 使用相同的 Canonical 输入、
ISA、forwarding、II 和 uarch 配置。

## 发射规则

每个 EXU 按 opcode/form 保存下一次允许发射的 self-II 截止周期。
实际队列发射采用下式，A 为候选指令，B 为同端口最近实际发射的指令：

```text
earliest(A) = max(last_issue(A) + II(A,A), last_issue(B) + II(B,A))
```

每项使用对应 producer 的发射时刻。其他指令的插入保留尚未满足的 self-II 限制，
完成事件也保留其有效截止周期。操作数就绪、端口接收带宽、EXQ 接收及容量分别检查。
EXQ 预测同时考虑已发射的 self-II 截止周期、队尾预测转换间隔和最近同 form 的
待发射指令。直接发射模式保留所选跨 FU 策略，并检查 opcode/form self-II。

Python 的实现位于 `core/uarch/exu.py`、`core/uarch/exq.py`；C++ 对应实现位于
`native/core/uarch/`。共享资源的其他持续限制仍需单独识别和标定。

## 验证范围

- Python 完整测试 426 项通过，配置了 Native runner。
- C++ CTest 8 项通过。
- 完整 workload 回归 25 个案例执行完成；修正前、修正后的双实现周期分别一致。
- 修正后的 25 个案例逐条 start/done 事件一致，比较周期、动态 ID、opcode、form 和端口。
- 修正前有 211 处同端口同 opcode/form 的发射间隔低于配置 self-II；修正后为 0。
- 25 个案例的周期变化检查均在现有容差内，4 个案例的精度退化检查未通过。

`tests/test_issue_intervals.py` 覆盖跨 FU 和同 FU 穿插、绝对发射时间、端口与 form
独立性、EXQ 预测及选择、完成后的有效截止周期，以及 VEXP/VDIV 的方向间隔。
`native/tests/components_test.cpp` 检查对应的 C++ 行为。

## 整体误差

21 个案例有 CAModel 参考周期，4 个案例只参与周期和双实现比较。
MAPE 对 21 个案例的相对绝对误差等权平均；WAPE 为总绝对周期误差除以总参考周期。

| 指标 | 修正前 | 修正后 |
| --- | ---: | ---: |
| MAPE | 4.738% | 5.199% |
| WAPE | 3.566% | 3.645% |
| 相对绝对误差中位数 | 3.750% | 3.687% |
| 最大相对绝对误差 | 14.120% | 14.120% |

有参考值的案例中，4 个误差减小、6 个误差增大、11 个不变。
全部 25 个案例中，11 个预测周期发生变化，14 个不变。
该结果表明 self-II 约束修正消除了局部违规，当前完整 workload 的平均误差有所增加。

## 逐例结果

表中误差为 `abs(prediction-reference)/reference`。

| workload | 参考周期 | 修正前周期 | 修正后周期 | 修正前误差 | 修正后误差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gelu_poly_i16_u1 | 446 | 404 | 404 | 9.42% | 9.42% |
| gelu_poly_i64_u1 | 1502 | 1319 | 1319 | 12.18% | 12.18% |
| gelu_poly_i96_u1 | 2245 | 1928 | 1928 | 14.12% | 14.12% |
| gelu_i16_u1 | 187 | 189 | 188 | 1.07% | 0.53% |
| online_update_i64_u1 | 443 | 434 | 437 | 2.03% | 1.35% |
| probe_src_fanout | 285 | 282 | 282 | 1.05% | 1.05% |
| probe_branch_live_range | 221 | 218 | 218 | 1.36% | 1.36% |
| probe_store_capture_reuse | 319 | 317 | 317 | 0.63% | 0.63% |
| vadds_longchain_i16_1x512 | 21823 | 22153 | 22153 | 1.51% | 1.51% |
| vadds_longchain_i64_8x64 | 37281 | 39536 | 39536 | 6.05% | 6.05% |
| vadds_longchain_i128_128x4 | 36641 | 32827 | 32827 | 10.41% | 10.41% |
| vexp_longchain_i16_1x512 | 95492 | 95492 | 95492 | 0.00% | 0.00% |
| vexp_longchain_i64_8x64 | — | 161198 | 161198 | — | — |
| vexp_longchain_i128_128x4 | — | 131137 | 131137 | — | — |
| gelu_poly_i96_u2 | 2021 | 1856 | 1856 | 8.16% | 8.16% |
| gelu_poly_i96_u3 | — | 1807 | 1794 | — | — |
| gelu_poly_i96_u4 | 1712 | 1777 | 1908 | 3.80% | 11.45% |
| gelu_poly_i96_u6 | — | 1746 | 1746 | — | — |
| gelu_poly_i96_u8 | 1718 | 1717 | 1695 | 0.06% | 1.34% |
| silu_i16_u1 | 160 | 154 | 156 | 3.75% | 2.50% |
| swiglu_i16_u1 | 180 | 172 | 170 | 4.44% | 5.56% |
| silu_i64_u1 | 386 | 411 | 412 | 6.48% | 6.74% |
| silu_i96_u1 | 538 | 582 | 580 | 8.18% | 7.81% |
| swiglu_i64_u1 | 480 | 469 | 464 | 2.29% | 3.33% |
| swiglu_i96_u1 | 678 | 661 | 653 | 2.51% | 3.69% |

精度退化检查未通过的案例为 `gelu_poly_i96_u4`、`gelu_poly_i96_u8`、
`swiglu_i64_u1`、`swiglu_i96_u1`。检查同时使用现有的绝对误差与相对误差增加容差。
`swiglu_i16_u1` 和 `silu_i64_u1` 的误差增加仍在该容差范围内。

`gelu_poly_i96_u4` 的预测从 1777 增至 1908 cycle，参考值为 1712。
该案例修正前存在 46 处 VDIV.fp32 self-II 违规；详细事件和前序同类指令见机器报告。
约束修正会改变混合流发射顺序及后续队列和寄存器资源竞争，总周期变化可以为正或负。
这些结果用于定位完整模型的误差，不能单凭总周期差异确定其他硬件资源的归属。

## 证据与复现

[机器报告](../../results/issue_history_20261020/analysis.json) 包含聚合指标、25 个案例、
局部违规样本、参数 SHA256、原始调度日志 SHA256、双实现事件对照和失败检查。
同目录保存四份 `current_metrics.json`、四份 `compare_summary.json`、代码补丁及
`verification.json`。本轮保留原始参数和参考基线，完整原始日志位于报告记录的实验目录。

```bash
VFSIM_NATIVE_RUNNER=/path/to/vfsim_native_json_runner python3 -m unittest discover -s tests -q
ctest --test-dir /path/to/build-native --output-on-failure
python3 tools/run_cost_model_regression.py --tier full --baseline /path/to/python-before/current_metrics.json --out-dir results/issue_history_python
python3 tools/run_native_cost_model_regression.py --tier full --runner /path/to/vfsim_native_json_runner --baseline /path/to/native-before/current_metrics.json --out-dir results/issue_history_native
```

两条完整回归命令会针对上述四个精度退化案例返回失败状态；功能测试和
Python/C++ 对齐结果独立记录。
