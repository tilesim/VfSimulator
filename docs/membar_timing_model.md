# 全局 Membar 时序建模

本分支同步主线提交 `76e739d` 的 C++ 全局 membar 模型，不引入 Python 或局部 UB 地址依赖实验。
正式预测入口仍为 `runCanonicalVfInfo()`，参数位于 `configs/uarch.json` 的 `membar_timing`。

## 参数

| 参数 | VLD_VST | VST_VLD |
| --- | ---: | ---: |
| release_latency | 18 | 7 |
| retire_latency | 20 | 9 |
| consumer_delay | 4 | 1 |
| next_issue_delay | 1 | 2 |

`admission_delay=1`。段内 LSU start 到下一 barrier issue 的反馈延迟为 LOAD=3、STORE=4。
这些值是 A5 CAmodel 用例的近似校准，不代表所有硬件的固定行为。

## 控制流程

1. 根据动态 `stream_seq` 封存 barrier 前的 LSU 段，后续段不污染等待集合。
2. barrier 发射需满足上一 barrier 退休及间隔、段内 LSU start 反馈、取指延迟、VF startup，且前序指令已通过 IDU 及传输。
3. 首个 barrier 检查前序 STORE start；后续 barrier 检查前一个 barrier 所阻塞类别的段内 LSU start。
4. issue 后满足 release_latency，且指定方向的前序 LSU 已完成，才能同步放行。控制单元在本周期 OoO 完成处理之前检查，不会提前同周期唤醒。
5. 消费者等待实际 release 加 consumer_delay；退休为实际 release 加 retire_latency 减 release_latency。
6. 尾部 barrier 退休计入 VF 总时间；`membar_history.json` 记录 issue、sync_release 和 retire。

Membar 不占 IDU/SHQ/LSQ/EXU 执行槽，不直接阻塞普通 compute。
配置字段不存在时保留旧模型兼容；存在但为 null、空对象、类型错误或缺少必填值时拒绝，不能静默回退。
本次未更改 ISA latency、forwarding、II 或算子回归 baseline。

## 验证

`native/MembarTimingTest.cpp` 覆盖双方向放行边界、连续八个 barrier、段内 LSU 乱序 start、延迟完成、IDU 等待和非法控制参数。

```bash
cmake -S native -B /tmp/vfsim_cpp_membar -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/vfsim_cpp_membar -j 4
ctest --test-dir /tmp/vfsim_cpp_membar --output-on-failure
```

跨语言完整 fixture 与验证工具保存在主仓 `vfinfo-core-api-unification` 提交 `76e739d`，
本纯 C++ 分支不复制 Python 验证入口。
