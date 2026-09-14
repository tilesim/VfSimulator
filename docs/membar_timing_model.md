# A5 全局 Membar 时序模型

## 本分支范围

ub-address-dependency-experiment 的 Python/C++ 全局模式增加控制单元的发射、同步放行和退休阶段。
移植自 MultiSoC 全局模型与 MultiSoC-membar 实验实现，适配本分支根目录 configs/uarch.json。
本分支没有 MultiSoC 选择器，不引入 A6 或 --soc 接口。

Python 局部 UB 地址依赖实验继续保留：移除方向性 barrier 后不再收取相应控制延迟，
冲突访存仍采用 producer.done+1 的保守边界。C++ 本轮仅同步全局模型，不新增局部 UB 分析。

## 参数合同

membar_timing 字段不存在时使用历史兼容模型；字段存在但为 null、空对象、非法类型或缺少参数时明确报错。
必填数值为非负 int64，不接受 bool；退休不得早于放行，next_issue_delay 至少为1。
本次不改变 ISA latency、forwarding、II，也不改变架构资源或寄存器数量。

| 参数 | VLD_VST | VST_VLD |
| --- | ---: | ---: |
| release_latency | 18 | 7 |
| retire_latency | 20 | 9 |
| consumer_delay（相对放行） | 4 | 1 |
| next_issue_delay（相对上一 barrier 退休） | 1 | 2 |

admission_delay=1；段内 LSU start 到下一 barrier issue 的反馈：STORE=4，LOAD=3。
这些是 A5 已有最小 CAmodel 用例的近似校准，不是通用硬件定律。

## 推进规则

1. IFU 按动态 stream_seq 接收 barrier，封存前一段 LSU；后续组不污染已封存状态。
2. 等前一个 barrier 退休及方向对应的 next_issue_delay。
3. 根据前一个 barrier 所阻塞的类别，等段内 LSU 全部 start，按最晚 start 加反馈延迟。
   首个 barrier 默认检查前序 STORE start，而不是等 STORE done。
4. 不早于 VF startup 和取到 barrier 后下一周期，且先前普通指令必须通过 IDU 及传输。
5. issue 后满足 release_latency，且指定方向的所有前序 LSU 完成，才 sync_release。
6. 在实际 release 后增加 retire_latency-release_latency 得到退休；后续 LSU 等 release+consumer_delay。

因此正常 VLD_VST 后的 store 可在退休后2周期开始；VST_VLD 后的 load 可在退休前1周期开始。
barrier 不进 EXU、不占 IDU/SHQ/LSQ 槽，不直接阻塞 compute。
尾部 barrier 退休计入 VF 结束；membar_history.json 记录 issue、sync_release、retire。
LSU start 通知后释放索引，段内状态按类别聚合，不保留无限增长的完整历史。

## 验证和复现

- tests/test_membar_timing.py：控制阶段边界、连续 barrier、段内乱序 LSU start、延迟完成、尾部 barrier、空/非法配置及兼容行为。
- native/MembarTimingTest.cpp：对应 C++ 控制阶段边界。
- tests/test_membar_native_parity.py：相同 Canonical 输入逐项比较 Python/C++ 总周期、start/done、barrier 事件。
- tests/fixtures/membar_timing/：可检出的单 barrier、连续8个、交错8个和编译器 spill 回放。
- tools/validate_global_membar.py：不依赖忽略的 results 目录，校验全局结果。
- tools/membar_fixture_export.py 与 native/MembarCanonicalTestRunner.cpp：测试专用 Canonical 序列化/执行器，保留现有 legacy runner 接口。

```bash
cmake -S native -B /tmp/vfsim_ub_membar_port -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/vfsim_ub_membar_port -j 4
ctest --test-dir /tmp/vfsim_ub_membar_port --output-on-failure
VFSIM_NATIVE_RUNNER=/tmp/vfsim_ub_membar_port/vfsim_membar_canonical_test_runner \
  python3 -m unittest discover -s tests
python3 tools/validate_global_membar.py --out-dir /tmp/ub-global-validation \
  --native-runner /tmp/vfsim_ub_membar_port/vfsim_membar_canonical_test_runner
```

历史 CAmodel 参考：单 barrier 110、连续8个336、交错8个339、spill391 cycles。
对应全局模型参考114/338/348/396 cycles，保存在 docs/validation/membar_timing_summary.json；
CAmodel 结果来源于之前的实验，本次不重新编译或运行硬件模拟器。
未自动更新任何算子回归 baseline。
