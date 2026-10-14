# LSU 与地址状态

`core/uarch/lsu.py::LSU` 维护 LOAD/STORE 等待队列、就绪状态、执行中访存和共享 UB
预算。指令类别来自 Profile，签名、模式及搬运预算来自 InstructionCatalog。

## 发射与资源

`update_ready()` 更新依赖和控制门槛，`_issue_ready_lsu()` 从就绪候选中选择指令。
LOAD 检查 startup、LSQ 接收时间和加载侧对齐状态；STORE 检查 vector/predicate
数据源、对齐消费组及 LSQ 接收时间。Membar 控制器提供后续访存门控。

默认每周期最多发射 2 条 LOAD、1 条 STORE，同时满足 2 个 UB slot 和 512 B 的
共享预算。每条事务的 `ub_transfer_bytes` 在入队时根据 Catalog 及 mode 解析。
普通访存预算为 256 B，DINTLV_B32 为 512 B。一个周期内两轮 LSU 仲裁共享这些计数。
预算不足时继续检查可容纳的候选；单条事务超过配置预算会报错。

候选排序参考资源压力和动态年龄。可用 vector 物理资源低于
`lsu_store_priority_preg_threshold` 时优先 STORE，其他情况优先 LOAD。
STORE 使用 SHQ 共用信用，LOAD 使用 LSQ 容量。

## 内存描述和 POST_UPDATE

Canonical `MemoryAccess` 提供 UB 对象、affine offset、span、访问方向、地址状态 ID
和指针更新量。offset/span 使用内存元素单位，`post_update_delta_bytes` 使用字节单位。
CCE parser 根据指针 dtype 和 Catalog 的增量编码将更新量转换为字节。

```cpp
vlds(value, ptr, 64, NORM, POST_UPDATE);
```

该访问使用 ptr 的当前位置，然后发布更新。对于 FP32 指针，增量 64 表示 256 B。
共享指针或别名通过 `address_state_id` 标识更新依赖；显式 offset 的访问保留其地址表达式。

`core/uarch/idu.py::AddressStateTracker` 保存每个地址状态最近一次更新的就绪时刻：

```text
ready_cycle = dispatch_cycle + idu_post_update_ready_latency
```

IDU 选择指令时检查关联地址状态，成功分发后立即发布更新。默认延迟为 1；同一地址
状态的后续访问需达到 ready_cycle，独立状态可以继续分发。该约束发生在 IDU，进入
LSQ 的指令按 LSU 自身依赖和资源规则仲裁。

普通 UB 访问顺序使用显式 Membar 描述。UB 区间冲突的自动分析超出当前执行能力范围。
地址更新状态和对齐状态分别维护：前者控制指针使用，后者控制加载初始化或存储 flush。

## 双结果和对齐访存

| 调用或模式 | Canonical 描述 | 执行语义 |
| --- | --- | --- |
| `vlds(even, odd, ptr, offset, DINTLV_B32)` | VLDS.fp32；mode=DINTLV_B32；两个 Register 输出；span=128 | 512 B 连续数据解交织，单条 LOAD，原子申请两个物理目的 |
| `vldas(state, ptr)` | VLDAS，align operation=load_init | 建立当前加载对齐生产者，进入 LOAD 路径 |
| `vldus(dst, state, ptr)` | VLDUS，align operation=load_use | 捕获 VLDAS 依赖，产生一个 vector 输出 |
| `vldus(dst, state, ptr, inc, POST_UPDATE)` | 增加地址状态和字节更新量 | 同时参与加载对齐与 IDU 地址约束 |
| `vstus(state, value, ptr, inc, POST_UPDATE)` | STORE，align operation=append | 加入当前存储状态组 |
| `vstas(state, ptr, inc, POST_UPDATE)` | STORE，align operation=consume | 封存并消费当前组，发布新组 |

VLDAS/VLDUS 支持 FP32、FP16、S32，latency 均配置为 9。
VLDAS→VLDUS 的状态 forwarding 配置为 1，表示加载内部等待的近似。
VLDUS 的普通数据 forwarding 通过 `forwarding_opcode=VLDS` 复用同 form 查询。
执行路径上没有 VLDAS 初始化的状态消费会报错。

DINTLV_B32 使用一条 Uop 和两个独立目的定义，两个结果共享该 Uop 的开始和完成时间。
其 512 B 共享预算使该事务单独占满一个周期的字节额度。
该预算表达资源近似；IDU 的 UOP-Split 硬件配额超出当前组件规则。

## PSTU 谓词存储

`pstu(state, predicate, ptr)` 读取 PredicateRegister，追加存储对齐组并隐式更新指针。
`vstas()` 消费该组。PSTU 的 latency 配置为 8，PSTU→VSTAS forwarding 为 1；
PSET→PSTU 使用 producer wildcard 的 2。

| form | 打包载荷 | 指针推进 | memory span |
| --- | ---: | ---: | ---: |
| uint32 / b32 | 8 B | 8 B | 2 元素 |
| uint16 / b16 | 16 B | 16 B | 8 元素 |

PSTU 的调度预算为 256 B。有效载荷、地址推进和 UB 调度预算在 Canonical/Catalog 中
分别表达。它读取一个谓词物理源，对齐状态通过分组记录表达；目的向量分配需求为零。

## PLT 与 FP16 打包模式

PLT 通过 Canonical 输入描述 Scalar count、PredicateRegister mask 和 Scalar remaining
两个输出。只有 mask 进入谓词资源池；标量数值与指针算术由输入层预先描述。
CCE parser 的 PLT 语法适配超出当前支持范围。

`VLDS.fp16` 的 `UNPK_B16` 将 64 个 16-bit 元素放入 B32 槽位，
`VSTS.fp16` 的 `PK_B32` 提取这些槽位的数据并按 predicate 写回。
两种模式的 span=64，逻辑范围为 128 B，调度预算各为 256 B。
FP16 的 POST_UPDATE 增量 64 对应 128 B。部分 mask 保持声明的调度预算。
数值转换由显式 VCVT 描述。

模式、form、操作数和 span 由 API 按 Catalog 统一检查。示例见
[v2_plt_packed_fp16.json](../tests/fixtures/canonical_vf_info/v2_plt_packed_fp16.json)。

## 测试

| 测试 | 覆盖 |
| --- | --- |
| [test_post_update_address_state.py](../tests/test_post_update_address_state.py) | 地址状态、别名、独立指针和 IDU 时序 |
| [test_vlds_deinterleave.py](../tests/test_vlds_deinterleave.py) | 双目的、原子资源、循环和 mode/span 校验 |
| [test_vldas_vldus.py](../tests/test_vldas_vldus.py) | 加载初始化、状态绑定和重新初始化 |
| [test_pstu.py](../tests/test_pstu.py) | 谓词源、状态分组、地址推进和屏障 |
| [test_packed_memory_modes.py](../tests/test_packed_memory_modes.py) | PLT 输出、FP16 模式和跨度 |
| [test_ub_shared_issue_slots.py](../tests/test_ub_shared_issue_slots.py) | 共享槽与 LOAD/STORE 仲裁 |

测量样本及近似范围见[指令时序与证据](instruction_timing.md)。
