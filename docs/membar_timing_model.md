# Membar 时序模型

`core/execution/membar.*::MembarController` 管理屏障接收、发射、同步放行和退休，并向 LSU 提供后续访存门控。Engine 每周期调用控制器，LSU 报告开始与完成进度。

`VLD_VST` 等待前序 LOAD 完成并控制后续 STORE；`VST_VLD` 等待前序 STORE 完成并控制后续 LOAD。COMPUTE 按自身数据依赖推进。

## 配置

基础 `configs/uarch.json` 的 `membar_timing` 提供时序参数。缺省时使用按指定方向排空前序访存的控制规则。

| 字段 | VLD_VST | VST_VLD | 含义 |
| --- | ---: | ---: | --- |
| `release_latency` | 18 | 7 | issue 到允许检查同步放行的最小延迟 |
| `retire_latency` | 20 | 9 | 相对最小放行时间的退休参数 |
| `consumer_delay` | 4 | 1 | 实际放行到后续受控访存可发射的延迟 |
| `next_issue_delay` | 1 | 2 | 前一个 barrier 退休到本方向可发射的延迟 |

`admission_delay=1` 控制接收到可发射的延迟。`lsu_start_to_next_issue` 的 STORE=4、LOAD=3 描述前段 LSU 开始到屏障发射的反馈。参数必须为非负整数，`next_issue_delay` 至少为 1，`retire_latency` 至少等于 `release_latency`。

## 阶段

控制器按 `stream_seq` 接收屏障并封存前段依赖，结合接收延迟、startup、LSU 开始反馈和前屏障退休确定 issue 门槛。指定类别的前序访存完成且达到 release 延迟后，发布同步放行。

```text
retire_cycle = release_cycle + retire_latency - release_latency
consumer_gate = release_cycle + consumer_delay
```

尾部屏障的 retire 参与 VF 结束周期。启用 timing 时，`membar_history.json` 输出 issue、sync_release 和 retire 事件。

`tests/membar_timing_test.cpp` 验证连续屏障、LSU 开始反馈、延迟完成、尾部屏障与配置边界；Canonical 测试输入位于 `tests/fixtures/canonical_vf_info/timing/`。
