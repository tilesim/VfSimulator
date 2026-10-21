# EXU 完成带宽验证

Softmax 的 12 个独立用例及 33 个参考案例的合并统计见
[Softmax 完成带宽与 inflight 验证](softmax_completion_20261020.md)。

验证日期为 2026-10-20，分支为 `dev_wch/refactor`。Python 与 C++ 的 EXU
采用每 EXU 每周期最多完成一条计算指令的约束，并保留 opcode/form self-II
及相邻指令转换 II。默认每 EXU inflight 上限为 7，输入覆盖值 0 表示无限制。

## 调度语义

EXU 发射时预留 `issue_cycle + latency` 对应的完成周期。候选指令的完成周期
与同一 EXU 的在途指令冲突时，候选指令推迟发射；连续冲突持续推迟，直到完成
周期空闲。指令 latency、forwarding 及 II 参数保持原值。

例如 VEXP.fp32 的 latency 为 16，在 cycle 100 发射会预留 116；VADD.fp32
的 latency 为 7，在 109 发射也会占用 116，因此需要等到 110 发射、117 完成。
若 117 也被占用，则继续等待。两个 EXU 可以在同一周期各完成一条指令。

ALU、SFU 和谓词计算共享所属 EXU 的完成带宽。计数单位为动态指令，多结果指令
占用一个完成周期。LSU 使用独立的访存完成规则。多条计算指令仍可重叠执行，
self-II、转换 II、单周期发射带宽、完成带宽和 inflight 容量分别参与调度。

Python 实现位于 `core/uarch/exu.py`、`core/uarch/exq.py`；C++ 实现位于
`native/core/uarch/exu.h`、`exu.cpp`、`exq.cpp`。
EXQ 预测读取已发射指令的完成预留状态，实际发射再次检查并预留。
完成事件释放预留和 inflight 计数，预测查询保持状态不变。

## 实验范围

固定完整回归集的 25 个 workload、循环参数及全部配置文件。21 个案例具有保存的
CAModel 参考周期，4 个案例用于周期及双实现对照。两组输入分别覆盖 inflight
上限为 0 和 7，并与相同 inflight 设置、相同 II 规则下的完成带宽无约束结果比较。

Python 与 C++ 各执行两组，共 100 次 workload 仿真，形成 50 组双实现对照。
两种实现都执行完整仿真并关闭日志文件输出。Python 通过只读发射观测记录每端口
峰值 inflight、完成周期冲突、self-II 违规和同端口同周期重复发射。

## 整体误差

MAPE 为 21 个案例相对绝对误差的等权平均；WAPE 为总绝对周期误差除以总参考周期。

| 每 EXU inflight 上限 | 完成带宽无约束 MAPE | 每周期一条完成 MAPE | 无约束 WAPE | 每周期一条 WAPE |
| --- | ---: | ---: | ---: | ---: |
| 无限制，输入值 0 | 6.903% | 5.238% | 3.749% | 3.600% |
| 7 | 5.199% | 4.632% | 3.645% | 3.575% |

无限制 inflight 下，13 个有参考值的案例误差减小，8 个不变，0 个增大；
15/25 个案例的预测周期变化，观测到单端口峰值 inflight 为 10。
inflight=7 下，11 个有参考值的案例误差减小，8 个不变，2 个增大；
15/25 个案例的预测周期变化。

inflight=7 下误差增加的两个案例为：

- `gelu_i16_u1`：预测 188 → 191，参考 187，相对绝对误差 0.53% → 2.14%。
- `swiglu_i64_u1`：预测 464 → 463，参考 480，相对绝对误差 3.33% → 3.54%。

当前四组设置中，inflight=7 加每周期一条完成的 MAPE 和 WAPE 最低。
默认配置保留 inflight=7，输入覆盖用于评估无限制 inflight 的敏感性。
完成带宽的约束改善了这组 workload 的误差；具体硬件完成带宽及 inflight 容量
与其他资源的关系仍需独立实验确认。

## 逐例周期

下表的“无约束”与“一条”分别表示同一 inflight 设置下的两种完成带宽设置。
无参考值案例使用 `—` 标记。

| workload | 参考周期 | cap=0 无约束 | cap=0 一条 | cap=7 无约束 | cap=7 一条 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gelu_poly_i16_u1 | 446 | 404 | 408 | 404 | 408 |
| gelu_poly_i64_u1 | 1502 | 1319 | 1331 | 1319 | 1331 |
| gelu_poly_i96_u1 | 2245 | 1928 | 1947 | 1928 | 1947 |
| gelu_i16_u1 | 187 | 171 | 178 | 188 | 191 |
| online_update_i64_u1 | 443 | 402 | 425 | 437 | 448 |
| probe_src_fanout | 285 | 282 | 282 | 282 | 282 |
| probe_branch_live_range | 221 | 218 | 218 | 218 | 218 |
| probe_store_capture_reuse | 319 | 317 | 317 | 317 | 317 |
| vadds_longchain_i16_1x512 | 21823 | 22153 | 22153 | 22153 | 22153 |
| vadds_longchain_i64_8x64 | 37281 | 39536 | 39536 | 39536 | 39536 |
| vadds_longchain_i128_128x4 | 36641 | 32827 | 32827 | 32827 | 32827 |
| vexp_longchain_i16_1x512 | 95492 | 95492 | 95492 | 95492 | 95492 |
| vexp_longchain_i64_8x64 | — | 161198 | 161198 | 161198 | 161198 |
| vexp_longchain_i128_128x4 | — | 131137 | 131137 | 131137 | 131137 |
| gelu_poly_i96_u2 | 2021 | 1784 | 1922 | 1856 | 1921 |
| gelu_poly_i96_u3 | — | 1742 | 1830 | 1794 | 1880 |
| gelu_poly_i96_u4 | 1712 | 1886 | 1886 | 1908 | 1886 |
| gelu_poly_i96_u6 | — | 1671 | 1732 | 1746 | 1732 |
| gelu_poly_i96_u8 | 1718 | 1638 | 1683 | 1695 | 1738 |
| silu_i16_u1 | 160 | 149 | 153 | 156 | 159 |
| swiglu_i16_u1 | 180 | 167 | 170 | 170 | 174 |
| silu_i64_u1 | 386 | 359 | 371 | 412 | 412 |
| silu_i96_u1 | 538 | 499 | 516 | 580 | 577 |
| swiglu_i64_u1 | 480 | 443 | 451 | 464 | 463 |
| swiglu_i96_u1 | 678 | 625 | 638 | 653 | 664 |

## 功能与回归检查

- Python 完整测试 435 项通过，配置了 Native runner。
- C++ CTest 8 项通过。
- 50 组 Python/C++ 的 `vf_end`、`cycles_executed`、`linear_inst_count` 全部一致。
- Python 观测到完成周期冲突 0、self-II 违规 0、同端口同周期重复发射 0。
- 每条观测指令满足 `done_cycle = start_cycle + latency`。
- 两组设置与各自上一轮相同 inflight 的指标比较，周期及精度容差检查均通过。

`tests/test_exu_completion.py` 的 9 项测试覆盖完成冲突、连续冲突、历史预留、
跨 FU 共享、双端口独立、端口选择、EXQ 预测和实际等待、流水重叠及状态释放。
`native/tests/components_test.cpp` 检查相同的 C++ 行为。

对照仓库保存的 `regression_suite/cases/baseline_canonical_membar.json`，
默认 inflight=7 加完成带宽约束仍有 3 项精度退化检查未通过：
`gelu_poly_i96_u4`、`gelu_poly_i96_u8`、`swiglu_i64_u1`。
该基线保存于本轮 II 规则修正之前；同 inflight 上一轮指标与此基线分别记录。
本次保留原有参考基线。

## 证据与复现

[机器报告](../../results/exu_completion_bandwidth_20261020/analysis.json) 保存四组
聚合指标、逐例周期及误差、峰值 inflight、参数 SHA256 和全部检查状态。
同目录的 `cap0/`、`cap7/` 分别保存 Canonical 输入、输入 SHA256、Python/C++ 指标
及同 inflight 上一轮比较。`baselines/` 保存比较用的固定指标快照，
`compare_reference_baseline.json` 保存对照仓库基线的检查。
`verification.json` 记录代码与二进制 SHA256、测试数量和验证范围，
`completion_bandwidth.patch` 保存本轮改动。

在仓库根目录执行，runner 指向包含完成带宽规则的本次 C++ 构建：

```bash
VFSIM_NATIVE_RUNNER=/path/to/vfsim_native_json_runner python3 -m unittest discover -s tests -q
ctest --test-dir /path/to/build-native --output-on-failure
python3 results/exu_completion_bandwidth_20261020/run_experiment.py --backend python --cap 0
python3 results/exu_completion_bandwidth_20261020/run_experiment.py --backend native --cap 0 --runner /path/to/vfsim_native_json_runner
python3 results/exu_completion_bandwidth_20261020/run_experiment.py --backend python --cap 7
python3 results/exu_completion_bandwidth_20261020/run_experiment.py --backend native --cap 7 --runner /path/to/vfsim_native_json_runner
python3 results/exu_completion_bandwidth_20261020/analyze.py
```

II 规则验证见 [II 发射规则与精度回归](issue_interval_regression_20261020.md)，
inflight 容量实验见 [EXU inflight 无上限对照](inflight_unlimited_20261020.md)。
