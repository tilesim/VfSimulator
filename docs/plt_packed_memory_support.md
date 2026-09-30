# PLT 与 FP16 打包访存合同

更新日期：2026-09-29。范围：PLT、VLDS.UNPK_B16、VSTS.PK_B32。
本次不实现任意 vector shape/layout cast，也不把未支持的 IR 改成普通 NORM。

## Canonical 输入

| 指令 | form | inputs | outputs | attributes |
|---|---|---|---|---|
| PLT | b8/b16/b32 | count: Scalar，role=scalar | mask: PredicateRegister，role=destination；remaining: Scalar，role=destination | 可保留 active_elements=37 供诊断，不参与时序计算 |
| VLDS | fp16 | memory: UB，role=memory | data: Register，role=destination | catalog_mode=UNPK_B16 |
| VSTS | fp16 | data: Register，role=source；mask: PredicateRegister，role=predicate | memory: UB，role=memory | catalog_mode=PK_B32 |

PLT count/remaining 接受 uint32/int32/int64；NPUIR index 需映射到明确的整数 dtype。
必须保留两个输出，不能把 remaining 当向量结果，也不能把 PLT 替换成 PSET/PGEALL。
只有 mask 进入谓词物理寄存器池；标量结果遵循当前 Core 的标量不计时约定，
不分配 vector/predicate preg。本模型不计算掩码位值、标量数值或标量流水时间。
如果 remaining 参与后续地址/循环次数，adapter 仍须解析出可执行的参数/affine 表达式，
不能假设 Core 会执行标量减法来计算该地址。

共享输入示例：
[v2_plt_packed_fp16.json](../tests/fixtures/canonical_vf_info/v2_plt_packed_fp16.json)。
该 fixture 的向量 shape 为 (64,)，dtype 为 fp16；模式属性不能省略。
PLT 当前以显式 Canonical/program 输入接入；CCE 的 `plt_b*(count, POST_UPDATE)`
语法适配不在本次实现范围内。CAModel 探针直接由 ccec 编译，不经过 VfSim CCE parser。

## 模式特殊性

- UNPK_B16 把连续的 64 个 16-bit 元素放入 256B 向量的稀疏 B32 槽位，
  并不是数值上的 FP16 -> FP32 转换。后续数值转换仍须显式 VCVT。
- PK_B32 从各 B32 槽位提取 16-bit 数据，按 predicate 控制写回连续内存；
  并不是普通的 128-lane NORM_B16 store。
- 两种模式均只有一个普通向量数据结果/输入，模式本身不增加额外 compute 指令。
- 当前只开放已验证的 fp16 形式；bf16/整数形式尚未在这些模式中开放。
- 满掩码的逻辑访问范围为 64 个 fp16 元素，即 128B。
  Canonical `span=64`、offset 按内存元素计；37 元素尾块不会缩小调度资源预算。
- `ub_transfer_bytes=256` 暂沿用单寄存器 LSU 的保守调度预算，**不是声称本次已测出
  256B 物理事务**。它与逻辑 span 是不同字段；当前每周期共享 512B 预算不变。
  减少为 128B 或调整并发能力必须另做吞吐实测。
- POST_UPDATE 仍按访问指针元素宽度换算：FP16 增量 64 -> 128B，
  不以寄存器物理宽度或调度预算替代地址更新量。

Python/C++ 的模式校验同时检查 load 输入和 store 输出的 span；dtype 校验只对
数据 Register/UB 生效，不能把 bool predicate 错误要求为 fp16。
CCE UB 参数保留真实元素 dtype，避免 half 指针在 canonical 中默认为 fp32。

## CAModel 证据

环境：CANN 9.0.0-beta.1，dav-c310-vec，Ascend950PR_9599，vec-misched=0。
探针由 [memory_probe.py](../cce_code/predicate_select_test/memory_probe.py) 生成；
输入为已知 FP16 数据，输出先填充不同的哨兵，逐字节比较整个 256B 输出区域。

```bash
python3 cce_code/predicate_select_test/memory_probe.py --kind unpack_pack_tail --dtype fp16 --offset 0 --iterations 1 --active-elements 37
python3 cce_code/predicate_select_test/memory_probe.py --kind unpack_pack_tail --dtype fp16 --offset 0 --iterations 1 --active-elements 64
```

脚本打印 artifacts 目录，并保存生成 CCE、host、commands.json、input/golden/output.bin、
原始 instr_log/instr_popped_log 与 validation.json。

| 有效元素 | 数值校验 | VF cycles | PLT start/done | UNPK load start/done | PK store start/done |
|---|---|---:|---|---|---|
| 37 | 通过，74B 写回且后续哨兵不变 | 50 | 1694 / 1700 | 1693 / 1702 | 1701 / 1710 |
| 64 | 通过，128B 写回且后续哨兵不变 | 50 | 见 validation.json | 见 validation.json | 见 validation.json |

37 元素样本原始日志关键字段：

```text
RV_SMOVI Sd[64]=0x25, IMM:0x25
RV_PLT Dtype: B32 Pd[1]
RV_VLDI Vd[0] #offset=4 dist:UNPK_B16
RV_VSTI Vd[0] Pg[1] dist:PK_B32
```

这里只确认 PLT.b32 单样本 latency=6，已加入 ISA；ALU/EXU01 暂沿用谓词计算指令
的放置假设。b8/b16 未独立测时序，仍走缺失参数 warning/fallback。
PLT forwarding/self-II 未独立校准，沿用 ParamDB fallback（不能把此探针的间隔
解释为最小 forwarding，因为 store 还等待 load）。
两种访存的本次 latency 均为 9，与普通 VLDS/VSTS.fp16 一致，暂复用已有时序参数。
模式间 forwarding、II、吞吐与端口占用尚未独立校准；不把本次功能覆盖称为完整时序标定。

## 验证

`tests/test_packed_memory_modes.py` 覆盖 PLT 的谓词依赖、标量不占 preg、
模式 span/form、错误 dtype/span 拒绝、共享 fixture，以及设置
`VFSIM_NATIVE_RUNNER` 后的 Python/Native 发射周期和 VF 总周期一致性。
Native `VfsimVfInfoApiTest` 直接读取同一 fixture 并检查错误类型拒绝。

NPUIR adapter 仍须显式映射 PLT 的两个结果和上述模式；本仓库不会自行解析 ave.hir。
