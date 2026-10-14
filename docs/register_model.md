# 寄存器与依赖模型

`core/uarch/registers.py::Registers` 负责动态值到物理资源的映射、数据依赖、源引用
释放、信用可见性和特殊对齐状态。`register_bank.py::PhysicalRegisterBank` 负责资源池的
容量、分配和安全回收。

## 身份和资源池

Canonical 的 `definition_id` 标识静态定义；动态展开结合 `iteration_path` 区分各次
循环实例。Registers 使用这对身份建立 RAT 映射。`logical_id` 表示源码逻辑身份，
源码到物理池的映射由定义及其动态生命周期决定。

vector 和 predicate 分别使用独立的物理资源池，默认容量为 68 和 32。
只有 `Register`、`PredicateRegister` 进入重命名资源池。
UB 对象、Scalar 和对齐状态由各自的语义路径处理。
IDU 原子检查一条指令的多个 vector/predicate 目的需求，再进行分发预留。

## 重命名与普通数据依赖

`rename()` 先解析输入定义对应的物理源，再为输出分配物理目的。
源引用记录 `(preg, generation)`，目的记录 producer Profile 和 pending 状态。
动态展开给出的最后使用信息决定动态映射何时结束。

指令开始时，`on_uop_start()` 发布 producer 的开始周期；消费者通过
`ready_cycle()` 和 ParamDB forwarding 查询可用时刻。
每个源独立参与就绪计算，消费者取所有约束的最大值。

predicate 操作数使用同一套定义、循环回边、重命名和引用释放机制。
PSET、VCMP、PAND、POR、MOVVP 和 PLT 产生或消费的谓词因此参与普通资源约束。
Scalar 的数值执行和标量寄存器容量超出该模型范围。

## 源引用释放

消费者开始时为物理源安排延迟事件：

```text
release_event_cycle = consumer.start_cycle + consumer_release_start_offset
```

默认 offset 为 4。事件到期时检查 generation 并减少 consumer count。
物理寄存器回到空闲池需满足：

- 当前 RAT 已结束对它的映射；
- producer 已发布开始状态，pending 已清除；
- consumer count 为 0；
- cycle 达到释放资格时刻。

generation 在同一个物理 ID 每次分配时递增，延迟事件据此检查分配版本。
`assert_start_release_integrity` 启用释放计数和过期事件检查。
执行完成、最后使用和资源资格共同推进生命周期，寄存器池逐项检查回收条件。

实际回收后，`idu_visible_preg_delay` 控制 IDU 可见信用的发布时间。
Registers 管理物理资源和源引用，SHQ 管理计算/存储共用信用，二者分别发布反馈。

## 对齐状态

`AlignProducerRecord` 保存一个状态生产者的指令身份、Profile 和开始/完成周期。
`AlignGeneration` 保存一个存储状态消费组的生产者集合及消费指令。
对齐状态采用独立依赖记录，容量和物理寄存器分配超出其建模范围。

加载路径：`VLDAS` 建立当前状态生产者，`VLDUS` 入队时捕获该生产者。
重新初始化会更新后续绑定；已入队消费者继续引用入队时的记录。
执行路径上缺少初始化的消费者会报错。

存储路径：`VSTUS`、`PSTU` 等 append 指令加入当前 generation，
`VSTAS` consume 指令封存该组并打开下一组。消费就绪周期取封存生产者的最大
`start + forwarding`；尚未开始的生产者会阻塞消费。

`bind_align_state()` 在入队时建立关系，`align_ready_cycle()` 提供就绪查询，
`on_uop_start/on_uop_done()` 更新记录。LSU 综合此结果进行访存仲裁。

## 代码与测试

- [registers.py](../core/uarch/registers.py)：重命名、依赖、事件及状态绑定。
- [register_bank.py](../core/uarch/register_bank.py)：容量和回收条件。
- [test_physical_register_bank.py](../tests/test_physical_register_bank.py)：资源池、generation 和释放。
- [test_predicate_registers.py](../tests/test_predicate_registers.py)：谓词资源、循环和消费者。
- [test_vldas_vldus.py](../tests/test_vldas_vldus.py)、[test_pstu.py](../tests/test_pstu.py)：状态初始化、分组及消费。

校准寄存器释放可采用单消费者、双消费者、原地覆盖、跨迭代覆盖、末端 store 和不同
间隔的压力探针。应同时记录物理 ID、最后消费者的开始时刻、资源回收事件和 IDU 停顿，
据此评估源码级生命周期近似。
