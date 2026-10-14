# 验证、日志与精度

本仓库通过单元测试检查数据合同和组件行为，通过固定 workload 检查周期及日志，
通过 CAModel 参考值评估预测误差。测试分组和辅助工具见 [tests/README.md](../tests/README.md)。

## Python 验证

```bash
python3 -m unittest discover -s tests -q
python3 tools/run_cost_model_regression.py --tier smoke --out-dir results/regression_smoke
python3 tools/run_cost_model_regression.py --tier full --out-dir results/regression_full
```

单元测试覆盖输入校验次数、Catalog、循环携带和 unroll、存储声明、物理资源、
组件生命周期、地址状态、对齐状态、Membar 和共享 UB 预算。
名称改写测试检查同一语义程序在改名后保持周期及物理调度一致。

回归脚本读取 `regression_suite/cases/cost_model_regression_cases.json`，默认基线为
`baseline_canonical_membar.json`，写出 `current_metrics.json` 和 `compare_summary.json`。
`--tier smoke` 选择核心用例，`--tier full` 覆盖完整集合。
`--update-baseline` 用于显式保存已确认的参考结果。

## 结果与日志

`CoreVfCostModel.run_vf_info()` 返回：

| 字段 | 含义 |
| --- | --- |
| `cycles_executed` | 排空时的仿真 cycle |
| `vf_end_cycle` | 最后指令完成或屏障退休的最大 cycle 加 VF drain |
| `linear_inst_count` | 程序静态布局中的指令计数 |
| `results_dir` | 输出目录，关闭文件输出时为 None |
| `trace_path` | Perfetto 文件路径，关闭文件输出时为 None |

| 文件 | 格式和用途 |
| --- | --- |
| `sim_history.json` | JSON 数组，指令 start/done/blocked 事件 |
| `start_by_cycle.json`、`done_by_cycle.json` | JSON Lines，按周期记录指令事件 |
| `idu_to_ooo.json` | JSON Lines，分发及资源、地址依赖快照 |
| `idu_address_blocked.json` | JSON Lines，地址状态阻塞 |
| `idu_resource_blocked.json` | JSON Lines，资源阻塞 |
| `vloop_trace.json` | JSON Lines，循环起点和迭代身份 |
| `membar_history.json` | JSON 数组，定时屏障的 issue/release/retire |
| `trace.json` | Perfetto JSON 时间线 |
| `model_warnings.json` | JSON 对象，存在缺失参数诊断时输出 |

默认 Canonical fixture 的 `cycles_executed=35`、`vf_end_cycle=47`。
使用以下命令可检查输出链路：

```bash
python3 main.py --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json --out_dir results/fixture
```

日志对比应统一配置、输入和计时口径，逐项检查 inst_id、stream_seq、iteration_path、
物理源/目的及开始/完成周期。绝对输出路径和运行时间戳作为运行元数据单独处理。

## 精度参考

下表取自 `regression_suite/cases/baseline_canonical_membar.json`，保存固定 workload
的模型基线和 CAModel 参考值。它用于回归及误差比较。

| case | 模型 cycles | CAModel cycles | 相对绝对误差 |
| --- | ---: | ---: | ---: |
| gelu_poly_i16_u1 | 404 | 446 | 9.42% |
| gelu_poly_i64_u1 | 1319 | 1502 | 12.18% |
| gelu_poly_i96_u1 | 1928 | 2245 | 14.12% |
| gelu_i16_u1 | 189 | 187 | 1.07% |
| online_update_i64_u1 | 434 | 443 | 2.03% |
| probe_src_fanout | 282 | 285 | 1.05% |

`abs(model-reference)/reference` 表示相对绝对误差。
cycle 回归验证结果稳定性，CAModel 对比评估时序近似。
测量 form、指令对、mask 模式、端口和编译器生成的动态流共同限定精度结论的适用范围。

模型输入描述源码或 Canonical 数据流；数值执行、完整编译器寄存器分配、独立 IFU
缓存、IDU UOP-Split 配额以及自动 UB 区间依赖分析超出当前执行能力。
特殊访存的事务预算和部分指令的时序使用近似，具体范围见[指令时序](instruction_timing.md)。

## 参数校准流程

1. 固定 workload、源码、输入、编译参数和数值 golden。
2. 核对编译后的 opcode、动态 ID、form 及实际依赖。
3. 通过独立指令、依赖链和间隔扫描分别测 latency、forwarding、II。
4. 对照 IDU、SHQ、EXQ、EXU、LSU 和资源回收时间线，定位假设影响的阶段。
5. 用固定参数重跑单元测试和回归，记录逐例误差及适用范围。
6. 检查跨 dtype、循环、寄存器压力和队列配置的行为，再保存确认的配置及参考值。

总周期、局部事件、数值结果和未覆盖条件共同构成校准记录。

## Native 对比

完整检出的 `native/` 提供 CMake 工程和 C++ runner。配置
`VFSIM_NATIVE_RUNNER` 后，可运行 `test_membar_native_parity` 及各指令的 parity 测试：

```bash
VFSIM_NATIVE_RUNNER=/path/to/vfsim_native_json_runner python3 -m unittest tests.test_membar_native_parity
```

双实现对比使用同一 Canonical fixture、参数和日志口径。
缺少 Native 环境的可选测试会跳过；结果报告应记录实际执行的覆盖范围。
