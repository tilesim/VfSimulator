# VLDS DINTLV_B32 双结果加载

## 支持范围

首版支持 FP32 指针及两个 FP32 vector 目的寄存器：

```cpp
vlds(even, odd, input, offset, DINTLV_B32);
vlds(even, odd, input, increment, DINTLV_B32, POST_UPDATE);
```

读取连续 512 B（128 个 FP32），偶数元素进入 even，奇数元素进入 odd。
两个目的寄存器必须不同。普通 offset 的单位为指针元素；POST_UPDATE 本次
使用指针当前地址，再按 increment 更新指针，沿用统一地址状态依赖规则。
其他 dtype 和 DINTLV_B8/B16 尚未纳入本次支持范围，不能套用 FP32 语义。

## 输入与执行合同

- opcode 仍为 `VLDS`，form 仍为 `fp32`。
- Canonical attributes 中 `catalog_mode="DINTLV_B32"` 选择双结果签名。
- memory operand 的 span 为 128 个 FP32 元素，即 512 B；两个 outputs
  是不同 value definition，producer_node_id 指向同一条 VLDS。
- Catalog 的 `memory_modes` 声明签名、支持 form 和 span；Python/C++ 共用
  JSON 声明，由生成工具产出 Native 表，不维护第二份手写签名。
- CCE 按声明中 mode 参数的位置和取值选择签名，再完整校验参数，不能仅凭
  五参数调用判断双结果，因为普通 POST_UPDATE 调用也有五个参数。
- Canonical 两端校验 mode、form、操作数数量/类型和 span。遗漏 mode 的双结果
  VLDS 不会按普通加载静默执行。
- 一条动态 LOAD，IDU 原子检查两个 vector preg credit，OoO 分别重命名两个
  outputs。消费者及最后使用释放分别追踪，复用现有通用多目的寄存器路径。
- latency 暂沿用 VLDS.fp32 的 9 cycles。占用一次 LOAD 发射和 UB slot，另计
  512 B 共享字节预算；后文说明字节预算的实验依据与假设边界。

## 验证依据

CAModel 探针及 golden 结果见 `docs/lsu_instruction_benchmark_20260924.md`。
FP32 offset 0 和 8 两组均通过数据校验。日志中只有一条 RV_VLDI，
dist=DINTLV_B32，ISU_RECV 含两个 DST_VREGS，ISU_ISSUE 标记 dual_dst=true；
并非 CCE wrapper 拆成两条加载。该日志观察到 issue 到 retire 为 9 cycles。

自动化测试：

```bash
VFSIM_NATIVE_RUNNER=/path/to/vfsim_native_json_runner \
  python3 -m unittest tests.test_vlds_deinterleave
```

覆盖普通/POST_UPDATE 重载、双输出 RAW、原子 credit、循环及 unroll 下的小资源池
回收、非法模式/签名/span 拒绝，以及 Python/Native 发射周期和 VF 总周期一致。
本次不修改 HiVM Adapter，也不新增 VLDSX2 正式 opcode。

## 双发探针（2026-09-24）

在当前 A5 配置下，CAModel 实测 DINTLV_B32 未双发；不能继续假设其分发能力
与普通 NORM 完全相同。最初探针阶段未改调度，后续按以下预算规则实现。

测试为手工 unroll=2 的 AA/BB/CC 分组：先两条独立地址加载，再计算，最后存储，
循环 8 次。双结果组保留四个输出的消费者，普通组保留两个，均对所有输出做
逐字节 golden 校验。编译器将两条加载地址转为不同 Sn 的 POST_UPDATE；不是
同一指针更新形成的串行链。

| 模式 | 动态 LOAD 数 | 同周期双 LOAD 的周期数 | VF cycles | golden |
|---|---:|---:|---:|---|
| NORM | 16 | 7 | 75 | 通过 |
| DINTLV_B32 | 16 | 0 | 104 | 通过 |

两组搬运字节数、计算/存储数量不同，不能把总周期差直接当作带宽性能差。

DINTLV_B32 首组证据（原始绝对 cycle）：

| 事件 | 第一条 ID63 | 第二条 ID64 |
|---|---:|---:|
| IDU dispatch | 1795 | 1796 |
| LDQ ISU_RECV / ISU_ISSUE | 1797 | 1798 |
| LSU SEND_UB_RD.PORT_0 | 1798 | 1799 |

ID64 已在 IDU 窗口内，但 cycle1795 被阻塞：

```text
Reason:UOP-Split number reached, slot:2
```

此时仍有 66 个空闲 vector preg，不是寄存器不足。后续指令组也出现相同阻塞。
普通 NORM 首组在 cycle1789 同时发射两条，后续多个周期继续双发
（具体为1789、1790、1791、1794、1795、1796、1797）。

结论范围：这套 CAModel 的端到端分发路径对双结果加载存在 UOP-Split 限制。
由于第二条指令尚未同周期进入 LDQ，本实验不能独立证明 LSU 的硬性双发能力，
也不能单独证明 UB 总带宽就是 512 B/cycle。建模应先考虑 IDU 的 split 配额，
不要仅凭字节数修改 UB slot 成本；DINTLV 与普通 LOAD/STORE 的混发规则还需隔离测试。

复现命令：

```bash
python3 cce_code/predicate_select_test/memory_probe.py --kind dual_pairs --offset 0 --iterations 8
python3 cce_code/predicate_select_test/memory_probe.py --kind norm_pairs --offset 0 --iterations 8
```

归档：`results/lsu_instruction_benchmark_20260924/dintlv_dual_issue/`。
`vfsim-memory-probe-102i11bn` 为双结果组；`vfsim-memory-probe-unz39bpd` 为普通组。
每组含 kernel.cce、host.cpp、commands.json、输入/输出/golden、validation.json
及 core0 的 IDU/ISU/LSU、instr_log、instr_popped_log。

## 后续采用的带宽模型

按当前实验约定，Python/Native 增加 `ub_bandwidth_bytes_per_cycle=512`。
这是独立于 load_ports、store_ports、ub_slots 的额外限制，三种约束必须同时满足。
搬运指令在 Catalog 中显式声明 `ub_transfer_bytes`：普通 VLDS/VSTS 及当前其他
STORE 为 256，DINTLV_B32 的模式声明覆盖为 512。它独立于输出数量和 memory span，
不再从物理寄存器推导。进入 OoO 时解析一次并写入 Uop，仲裁只读取 Uop 字段；
Native lowering 保留 catalog_mode，确保模式选择不会丢失。
这是传输预算，不是有效地址 span，广播、ONEPT、部分 mask 暂不减少预算。

- 普通 LOAD + LOAD：512 B，允许。
- 普通 LOAD + STORE：512 B，允许。
- DINTLV_B32：512 B，单独发射；下一 cycle 可再发一条。
- DINTLV_B32 + LOAD/STORE：超过预算，不允许。
- 同周期 compute 前后两次 LSU 仲裁共用计数，下一 cycle 才重置。

预算不足时可以继续寻找放得下的候选，保留现有寄存器压力阈值优先级。
不支持一条搬运跨多个周期分摊预算；若预算小于一条事务，明确报错，避免死锁。
此模型并未复现 IDU UOP-Split 配额。DINTLV 与 STORE 不能同发属于采用的
512 B 共享预算假设，尚不是混发探针的实测结论。
