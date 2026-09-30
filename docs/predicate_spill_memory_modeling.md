# PSTS / PLDS 谓词 spill 建模

## 输入与执行合同

```c++
psts(predicate, pointer, byte_offset, NORM);
plds(predicate, pointer, byte_offset, NORM);
```

- PSTS：PredicateRegister 输入，UB 输出，STORE 类，走 LSQ/store port。
- PLDS：UB 输入，PredicateRegister 输出，LOAD 类，走 LSQ/load port。
- 不使用 vector_align，没有 append/consume 分组，不分配 vector preg。
  PLDS 的目的值参与谓词 SSA、循环版本化和物理谓词重命名；消费者引用按 start+4 释放。
- 保存/恢复完整的 256-bit 谓词位图，即 32 B。B32 掩码仍占一整个谓词寄存器，
  每四个 byte lane 中的一个 bit 有效；不同于 PSTU 的有效位紧密打包。
- Catalog 使用固定 `b8` form 表示原始位图传输，不表示把谓词转为向量 uint8。
  `pset_b8/b16/b32` 的输出都可以通过这两条指令保存/恢复。
- 首版只接受 NORM，额外 POST_UPDATE 参数或其他 mode 明确拒绝。
- UB 数据顺序仍由 Membar 控制。PSTS→PLDS 是内存同步，不伪造寄存器 forwarding。

## 字节地址与带宽预算

第三个 CCE 参数按**字节**计，不随 `uint32_t*` 的元素宽度乘四。
Catalog 声明 `memory_address_unit_bytes=1`、`memory_span_by_form.b8=32`。
Canonical 对应 `MemoryAccess.address_unit_bytes=1`、`span=32`；两端 validator
均检查合同。未指定 address_unit_bytes 的既有输入继续使用内存元素单位。
该扩展在 JSON 编解码、Python/Native 对象和 Python lowering 中保留。

指针 alias 的 C 指针运算仍先按元素宽度换算。例如 `slot = base + 8`，base 为
uint32_t 指针，`psts(p, slot, 32, NORM)` 相对 base 的字节偏移是 64，不是 160。

**调度近似**：沿用现有 PSTU 等小粒度 LSU 的 256 B/条 UB 事务预算，不将其宣称
为 32 B 有效载荷的实际端口带宽。传输预算由 Catalog 显式声明，不由 preg 数量推导。
精确小事务吞吐率及与其他 LSU 的混合端口限制仍需专项校准。

## A5 实测参数

2026-09-30，Ascend950PR_9599 CAModel，ccec dav-c310-vec、misched=0。
小型无循环探针消除了原 GeLU spill 例中反复 Membar 的等待干扰。

| 参数 | 配置 cycle | 证据/范围 |
|---|---:|---|
| PLDS latency | 9 | 独立 PLDI 样本 start→done=9；存在端口/退休竞争时不保证观测间隔恒定 |
| PSTS latency | 9 | 小型探针以及原显式 spill 日志的 24 条 PSTI 均为 9 |
| PSET_B8/B16/B32 → PSTS | 4 | 三种宽度分别测试，逐字节 golden 通过 |
| VCMP_EQ.fp32 → PSTS | 4 | VCMP 1715，PSTI 1719 |
| PLDS → VSEL.fp32 | 6 | PLDI 1691，VSEL 1697 |
| PLDS → VADDS.fp32 | 6 | PLDI 1692，VADDS 1698 |
| PLDS → PAND.b8 | 6 | PLDI 1691，PAND 1697 |
| PLDS → VCMP_EQ.fp32 | 6 | PLDI 1692，VCMP 1698 |
| PLDS → PSTS | 8 | PLDI 1693，PSTI 1701 |
| PLDS → VSTS.fp32（mask 输入） | 8 | 双 VLDS 保留后，PLDI 1694，使用其掩码的 VSTI 1702 |

没有给所有未知 consumer 写 wildcard 来假装已测全；未列出的组合继续使用已有
timing fallback 并 warning。LSU 不走 compute EXU 的成对 II 仲裁，连续发射由
load/store port、UB slot、共享带宽和依赖约束；未添加虚假的 compute II 测量值。

## 复现与原始证据

```bash
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_compare_store --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_pset_store --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_pset_store16 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_pset_store32 --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_store --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_select --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_compute --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_logic --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_compare --iterations 1
python3 cce_code/predicate_select_test/memory_probe.py --kind predicate_load_masked_store --iterations 1
```

每次输出 `Artifacts:`，包含实际 kernel.cce、host.cpp、commands.json、input/golden/output.bin、
validation.json、core0 instr_log/instr_popped_log/LSU/EXU 日志。golden 为逐字节比较，
PSTS 测试同时检查 32 B 写入区间前后的 guard；PLDS 使用交替 lane 的非全真掩码。

本次原始路径（不随 Git 分发；可用上述命令重新生成）：

| probe 后缀 | `/tmp/vfsim-memory-probe-` 路径后缀 | golden |
|---|---|---|
| compare_store | gvmom_yi | PASS |
| pset_store / pset_store16 / pset_store32 | o7biew6a / zj4nu1l4 / 5fgfa93c | PASS |
| load_store | 0ddrgpka | PASS |
| load_select | i_tldy4o | PASS |
| load_compute | _30f15ks | PASS |
| load_logic | 43xut8b4 | PASS |
| load_compare | elvi6z8m | PASS |
| load_masked_store | vvb0nm6x | PASS |

早期 masked-store 探针中编译器删除了未使用的另一条 VLDS，PLDI 与剩余 VLDS 同周期
发射，观察到 PLDI popped→done=10、masked VSTI 间隔=9。保留两个 vector load
的实际消费者后，PLDI 在下一拍从 LDU0 进入，恢复为 latency=9、forwarding=8。
这说明不能用带其他端口竞争的单个时间差直接覆盖基础参数；当前不额外建模该一拍差异。

参考原例：`cce_code/gelu_grad/log_aligned/u8_explicit_spill.cce`，对应日志在
`results/ub_address_dependency_experiment/GeLU_grad/experiments/u8_explicit_spill/logs/`。
其中 PSTS/PLDS 编译为 RV_PSTI/RV_PLDI，spill offset 为 -1376/-1344/-1408。
原例 PLDI 的 popped→done 包含 Membar/LSU 等待，不能当成固定 latency。
原文件还含前端尚不能直接识别的固定地址局部 UB buffer
`__ubuf__ unsigned int *spill = (...)get_imm(0x40000)`；本次没有修改原文件或宣称
整份重建 CCE 已可直接预测。专项测试使用明确声明的 UB 输入，覆盖相同的谓词 spill 链。

## 自动测试

`tests/test_predicate_spill.py` 覆盖字节地址、负 offset、指针 alias、JSON 往返、
谓词 bank、80 轮 spill/reload 回收、Membar、forwarding、非法 mode/类型/单位。
设置 `VFSIM_NATIVE_RUNNER` 时逐条比较 Python/Native 开始 cycle 和 VF 总 cycle。
Native `PredicateRegisterTest` 独立检查 PLDS 的 bank 分配及缺失字节单位的拒绝。

```bash
VFSIM_NATIVE_RUNNER=/tmp/vfsim-packed-native/vfsim_native_json_runner \
  python3 -m unittest tests.test_predicate_spill -q
```
