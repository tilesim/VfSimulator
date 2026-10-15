# 寄存器与依赖模型

`native/core/uarch/registers.*::Registers` 管理动态定义到物理资源的映射、数据依赖、源引用释放、信用可见性和对齐状态。`register_bank.h::PhysicalRegisterBank` 管理资源池的容量、分配和回收。

## 动态身份与资源

Canonical 的 `definition_id` 标识静态定义，循环展开结合迭代身份区分动态实例。`logical_id` 描述源码逻辑身份。vector 与 predicate 使用独立物理资源池，默认容量分别为 68 和 32。

`rename()` 解析输入的物理源，为输出申请物理目的，并记录 producer、generation 和待开始状态。指令在 SHQ、EXQ、EXU、LSU 和完成跟踪之间共享同一 Uop。Scalar 数值和标量寄存器容量超出当前建模范围。

`onStart()` 发布 producer 开始周期。消费者通过 `readyCycle()` 及 ParamDB forwarding 查询源就绪时刻，各源约束共同决定最早可发射周期。predicate 使用同一套动态定义、循环回边、重命名及引用释放机制。

## 释放与信用

消费者开始后，以 `consumer_release_start_offset` 安排源引用释放事件。事件记录物理 ID 和 generation，以区分同一 ID 的不同分配版本。回收要求 RAT 映射结束、producer 已开始、消费者引用归零并达到释放资格时刻。

资源回收后，`idu_visible_preg_delay` 决定信用反馈给 IDU 的时刻。SHQ 信用由 SHQ 独立管理。IDU 在分派前检查 vector/predicate 目的需求，并考虑传输中的预留。

## 对齐状态

`AlignProducerRecord` 记录对齐生产者的指令身份和开始/完成时间，`AlignGeneration` 记录存储消费组。对齐状态使用独立依赖记录，容量与物理分配超出当前资源模型范围。

VLDAS 建立加载状态生产者，VLDUS 入队时捕获该生产者。重新初始化更新后续绑定，已入队消费者继续使用原记录。缺少初始化的状态消费会报错。

VSTUS、PSTU 等 append 指令加入当前存储组；VSTAS 封存并消费该组，随后打开下一组。`alignReady()` 汇总组内 producer 的开始和 forwarding 约束，供 LSU 仲裁。

相关验证位于 `native/tests/predicate_register_test.cpp`、`components_test.cpp` 和 `input_api_test.cpp`。
