# LSU 与地址状态

`native/core/uarch/lsu.*::LSU` 维护 LOAD/STORE 等待队列、就绪状态、执行中访存及共享 UB 预算。指令类别来自 Profile，模式、访存跨度和搬运预算来自 Catalog。

## 发射约束

`updateReady()` 更新数据依赖与控制门槛，`issue()` 选择可发射访问。LOAD 检查 startup、LSQ 接收时间和加载对齐状态；STORE 检查 vector/predicate 数据源、对齐消费组和接收时间。Membar 提供后续访存门控。

默认每周期最多发射 2 条 LOAD、1 条 STORE，同时满足 2 个 UB slot 和 512 B 共享预算。普通访问的调度预算为 256 B，DINTLV_B32 为 512 B。一个周期内的两轮仲裁共享计数；单条事务超过配置预算时会报错。

可用 vector 物理资源低于 `lsu_store_priority_preg_threshold` 时优先 STORE，其他情况优先 LOAD。STORE 占用 SHQ 共用信用，LOAD 受 LSQ 容量约束。

## 地址与依赖

Canonical `MemoryAccess` 显式描述 UB 对象、affine offset、span、访问方向、地址状态及更新量。offset/span 使用内存元素单位，`post_update_delta_bytes` 使用字节单位。输入程序负责提供已经解析的地址语义。

`native/core/uarch/idu.*::AddressStateTracker` 保存地址状态最近一次更新的就绪时刻：

```text
ready_cycle = dispatch_cycle + idu_post_update_ready_latency
```

IDU 成功分派后发布更新，共享 `address_state_id` 的后续访问受到该时刻约束。独立状态可分别推进。普通 UB 访问的同步由显式 Membar 表达，自动区间冲突分析超出当前执行能力范围。

## 多结果与对齐模式

DINTLV_B32 描述一条 LOAD 的两个 vector 结果，IDU 原子检查目的资源，两个结果共享指令开始和完成时间。

VLDAS/VLDUS 维护加载初始化依赖。VSTUS、PSTU 追加存储对齐组，VSTAS 消费封存组。PSTU 读取谓词源并更新地址状态，其有效载荷、指针推进和 UB 调度预算分别表达。

PLT 可产生 predicate mask 和 scalar remaining；mask 使用谓词资源池。FP16 的 UNPK_B16 与 PK_B32 模式由 Catalog 检查 form、操作数和 span。示例见 [v2_plt_packed_fp16.json](../native/tests/fixtures/canonical_vf_info/v2_plt_packed_fp16.json)。

地址与依赖测试位于 `native/tests/address_state_test.cpp`、`components_test.cpp` 和 `input_api_test.cpp`。
