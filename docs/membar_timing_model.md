# Membar 时序模型

`core/execution/membar.py` 管理屏障接收、发射、同步放行和退休，并对后续访存提供
门控。LSU 报告开始和完成进度，Engine 安排控制器每周期更新。
普通 UB 访存的额外同步通过 CanonicalMembar 节点表达。

## 方向与配置

`VLD_VST` 等待前序 LOAD 完成并控制后续 STORE；`VST_VLD` 等待前序 STORE 完成并
控制后续 LOAD。COMPUTE 根据自身数据依赖推进。
默认 `uarch.json` 提供 `membar_timing` 对象，Engine 据此创建 `TimedMembarController`。
该字段缺省时创建 `MembarController`，按指定方向的前序访存排空控制后续访问。

| 字段 | VLD_VST | VST_VLD | 含义 |
| --- | ---: | ---: | --- |
| `release_latency` | 18 | 7 | issue 到允许检查同步放行的最小延迟 |
| `retire_latency` | 20 | 9 | 相对最小放行时间的退休参数 |
| `consumer_delay` | 4 | 1 | 实际放行到后续受控访存可发射的延迟 |
| `next_issue_delay` | 1 | 2 | 前一个 barrier 退休到本方向 barrier 可发射的延迟 |

`admission_delay=1` 控制接收到可发射的延迟。
`lsu_start_to_next_issue` 中 STORE=4、LOAD=3，控制前段 LSU 开始到 barrier 发射的反馈。
数值字段要求非负 int64；`next_issue_delay` 至少为 1，退休参数需满足
`retire_latency >= release_latency`。显式提供空对象或无效配置会报错。

## 推进过程

1. 按 `stream_seq` 接收屏障并封存前一段 LSU 的开始依赖。
2. 结合接收延迟、VF startup、前一个屏障退休和方向延迟，确定最早发射时间。
3. 等待段内需要观察的 LSU 开始；以最晚 start 加 LOAD/STORE 反馈更新发射门槛。
   首个屏障采用前序 STORE 的开始反馈。
4. 等待前序普通指令通过 IDU 和传输，再发布 `issue`。
5. 达到 `issue + release_latency` 且指定类别的前序 LSU 完成后，发布 `sync_release`。
6. 根据实际 release 确定 retire 和 consumer gate，按 cycle 发布退休及后续放行。

```text
retire_cycle = release_cycle + retire_latency - release_latency
consumer_gate = release_cycle + consumer_delay
```

屏障由执行控制器保存，其节点通过取指流进入控制器。
后续组与封存的前序组分别维护；LSU 开始后更新段内聚合记录。
尾部屏障的 retire 参与最终 VF 结束周期。
`membar_history.json` 记录 `issue`、`sync_release`、`retire`。

## 固定验证用例

[validation/membar_timing_summary.json](validation/membar_timing_summary.json)保存本仓库
的 fixture、Python 参考周期和 CAModel 观测值。

| fixture | Python VF cycles | CAModel 参考 cycles |
| --- | ---: | ---: |
| single.cce | 114 | 110 |
| clustered8.cce | 338 | 336 |
| interleaved8.cce | 348 | 339 |
| spill.cce | 396 | 391 |

Python 值可通过当前代码和 [fixtures/membar_timing](../VFtest/cce/membar_timing)
重现。CAModel 值来自保留的测量记录，描述这些用例下的观测。

```bash
python3 -m unittest tests.test_membar_timing
python3 main.py --cce VFtest/cce/membar_timing/single.cce --out_dir results/membar_single
```

[test_membar_timing.py](../tests/test_membar_timing.py)覆盖连续屏障、乱序 LSU start、
延迟完成、尾部屏障和配置边界。
[test_membar_native_parity.py](../tests/test_membar_native_parity.py)在配置 Native runner 后
比较同一输入的周期、开始/完成和屏障事件。
