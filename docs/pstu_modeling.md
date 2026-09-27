# PSTU 谓词存储建模

## 输入与依赖

支持 `pstu(state, predicate, ptr)`，随后由 `vstas(state, ptr, 0, POST_UPDATE)`
消费当前 align-state 分组。指针类型支持 `uint32_t*` 和 `uint16_t*`。

- PSTU 是 STORE，进入 LSQ，消耗 store port、UB slot 和声明的传输预算。
- predicate 是真实的 PredicateRegister 输入，由谓词物理寄存器 bank 重命名；
  PSTU 不分配 vector 或 predicate 目的寄存器。
- state 是无容量约束的依赖对象，不进入 RAT、不分配物理寄存器。
- PSTU 按动态入队顺序 append 当前 generation；VSTAS 入队时封存 producer 集合，
  后续 PSTU 只能加入新组，不能污染前一组的等待集合。
- PSTU 使用普通 STORE 的源寄存器 ready 判定；VSTAS 使用已有的多 producer
  align-state ready 判定，不新增 opcode 专用调度分支。
- Membar 按 STORE 类别等待 PSTU/VSTAS 完成。谓词源引用沿用消费者 start+4 释放。

## 参数与证据

| 参数 | 当前取值 | 依据 |
|---|---|---|
| PSTU latency | 8 | 用户指定的首版近似；CAModel 样本存在 8/9，不宣称恒定实测值 |
| PSTU -> VSTAS forwarding | 1 | 现有探针中 VSTAS 在最后一条 PSTU 发射后下一拍发射，不等 done |
| PSET -> PSTU forwarding | 2 | 复用现有 PSET producer wildcard 参数 |
| VCMP 等 -> PSTU forwarding | 显式表优先，否则默认 latency-3 | 缺失参数仍输出 warning，未单独实测校准 |
| PSTU UB 预算 | 256 B/条 | 复用现有小粒度 STORE 事务计费近似，非有效载荷大小的实测结论 |

PSTU 不经过 compute EXU 的成对 II 仲裁；连续 STORE 发射仍由 store port、UB slot、
共享 UB 字节预算和依赖共同约束。当前一个 store port 允许无其他阻塞时逐 cycle 发射。

`configs/instruction_catalog.json` 维护签名、align 操作、传输预算和按 form 的地址语义：

| form | predicate 打包字节数 | 隐式指针推进 | memory span（元素数） |
|---|---|---|---|
| uint32 | 8 B | 8 B | 2 |
| uint16 | 16 B | 16 B | 8 |

隐式推进被转换为 Canonical 的 `post_update_delta_bytes`，由统一地址状态机制处理。
Canonical入口同时接受b16/b32，分别与uint16/uint32使用相同latency、span和推进值。
到VSTAS的同位宽b/uint/fp形式均显式配置forwarding=1，不依赖全局form猜测。
Python/C++ validator 均检查该推进值和 span。地址载荷大小与 UB 事务预算是两个字段，
不得用物理目的寄存器数量推导。额外 POST_UPDATE 参数或非变量指针表达式明确拒绝。

## 验证与复现

硬件探针和 golden 依据见 [搬运指令测试记录](lsu_instruction_benchmark_20260924.md)。
PSTU 探针入口：`cce_code/predicate_select_test/memory_probe.py --kind pstu`。
原始结果在 `results/lsu_instruction_benchmark_20260924/`，该结果目录不随 Git 分发。

`tests/test_pstu.py` 覆盖 B16/B32、PSET/VCMP forwarding、无 vector 目的分配、
循环、多 state、多 generation、Membar、非法地址合同，并可逐指令对比 Python/Native。

```bash
python3 -m unittest tests.test_pstu
VFSIM_NATIVE_RUNNER=/tmp/vfsim-predicate-foundation-native/vfsim_native_json_runner python3 -m unittest tests.test_pstu
```
