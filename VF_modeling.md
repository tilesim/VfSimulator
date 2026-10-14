# VF 建模说明

VfSimulator 从 Canonical 程序的数据流和控制结构出发，建立动态指令、物理资源及
周期状态。预测范围为 VF 的向量执行阶段，结果包含启动、指令执行、屏障尾部和 drain。

## 程序与动态身份

Canonical 的 `context` 保存 instruction、loop 和 membar，`values` 保存值定义，
`storage_objects` 保存 UB 对象。operand 通过 value_id 引用 definition，producer
关联生成该定义的节点。循环携带关系显式描述 entry、back-edge 和 exit。

存储类别通过 `StorageKind` 声明。Register 与 PredicateRegister 参与物理重命名，
UB 保存对象身份和访存描述，Scalar 提供参数及操作数信息。
输入层保留源码位置供错误和警告追溯。

`prepare_program()` 转换 Canonical 表示并生成静态布局；`DynamicExpander` 展开循环
和 unroll，记录迭代路径、动态指令顺序及最后使用。
动态值身份使用 `(definition_id, iteration_path)`。

详细数据和展开规则见 [输入 API](docs/input_api.md)与[执行模型](docs/execution_model.md)。

## 指令流和控制

IFU 顺序交付准备好的动态指令，IDU 按窗口、宽度、循环开放、资源信用和地址状态
检查队头，成功分发后进入传输与后端接收。Engine 为传输中的指令预留目的寄存器、
SHQ/LSQ 容量和信用，接收时进行重命名及入队。

LoopControl 维护 VLOOP 起点、body 开放、迭代步距及后续 block 的触发时刻。
Membar 控制器观察指定类别的前序访存，并对后续访存提供门控。
Engine 每 cycle 安排信用、传输、分发、释放、完成及计算/访存仲裁。
组件间的同周期可见性由这些阶段的顺序确定。

## 重命名、依赖和回收

Registers 为每次动态定义建立 RAT 映射，记录物理源、目的、producer 和 consumer。
vector 与 predicate 使用独立资源池，IDU 原子检查多目的需求。
生产者开始后发布开始周期，消费者按 producer start 加 forwarding 查询数据就绪。

消费者开始时安排源引用释放事件：

```text
release_event_cycle = consumer.start_cycle + consumer_release_start_offset
```

默认 offset 为 4。回收条件包括动态映射结束、pending 清除、consumer count 为 0 和
释放资格到期。generation 标识物理 ID 的分配版本，延迟事件据此检查归属。
信用释放通过可见延迟反馈给 IDU。

加载对齐状态捕获初始化生产者，存储对齐状态保存 append/consume 分组。
LSU 根据状态生产者的开始时间与 forwarding 检查消费就绪。
具体生命周期见 [寄存器模型](docs/register_model.md)。

## 计算路径

计算指令使用 `SHQ → OoO → EXQ → EXU`。
SHQ 更新源依赖就绪；OoO 根据合法端口、FU 轮转、EXU0 预留和 EXQ 容量选择指令；
EXQ 维护每端口的 FU 队列与接收时间；EXU 维护 II、在执行容量、开始和完成。
关闭 EXQ 队列模型时使用直接 `SHQ → EXU` 路径。

latency 描述开始到完成的周期数，forwarding 描述 producer 到 consumer 的可用间隔，
II 描述同端口相邻指令的发射间隔。队列模型的计算消费者在 SHQ 采用
`max(0, forwarding-1)`，再施加 EXQ 接收延迟。

## 访存路径

LOAD/STORE 进入 LSQ，由 LSU 综合数据依赖、对齐状态、Membar、发射端口及 UB 预算
仲裁。默认每周期提供 2 个 LOAD 端口、1 个 STORE 端口，以及共享的 2 槽和 512 B 预算。
STORE 使用 SHQ 共用信用。资源压力阈值决定 LOAD/STORE 的优先级，再按动态年龄排序。

`MemoryAccess.offset` 和 `span` 使用内存元素单位，指针更新量使用字节单位。
IDU 的 AddressStateTracker 在成功分发 POST_UPDATE 后发布地址就绪时间；
后续同地址状态访问等待 `dispatch + idu_post_update_ready_latency`，默认延迟为 1。

模式的操作数数量、span 和传输预算由 Catalog 声明。
DINTLV_B32 的一条 LOAD 产生两个 vector 结果并计入 512 B 预算；PSTU 打包谓词并
更新地址与存储状态；UNPK_B16/PK_B32 描述 FP16 的槽位布局与打包访问。
具体规则见 [访存模型](docs/lsu_model.md)。

## 屏障和结束周期

定时 Membar 包含接收、issue、sync_release 和 retire。
`VLD_VST` 等待前序 LOAD 并控制后续 STORE，`VST_VLD` 等待前序 STORE 并控制后续 LOAD。
release 检查最小延迟和前序访存完成，retire 与 consumer gate 按实际 release 计算。
参数和阶段边界见 [Membar 时序](docs/membar_timing_model.md)。

执行组件发布完成事件，Engine 的 `active_uops` 登记尚未完成的指令。
排空覆盖 IFU、IDU、传输、执行状态和屏障。
`cycles_executed` 为排空时的 cycle，`vf_end_cycle` 取最后指令完成或屏障退休的最大
cycle，再加 `vf_drain_cost`。达到 `params.max_cycles` 时仍未排空会报错。

## 参数和适用范围

InstructionCatalog 声明语义，ParamDB 查询指令 Profile、forwarding 和 II。
微架构配置提供容量、传输、调度和控制参数。缺失时序采用规定的回退值，并输出诊断。
配置和测量范围见 [配置参数](docs/configuration.md)与[指令时序](docs/instruction_timing.md)。

输入描述源码或 Canonical 层的数据流。数值执行、完整编译器寄存器分配、host/GM
搬运、独立取指缓存、IDU UOP-Split 配额和自动 UB 区间依赖分析超出当前模型范围。
普通 UB 同步由显式 Membar 描述。显式 dependency 和 unroll 形式需满足 Core 能力检查。

理论场景放宽部分资源和控制约束，用于比较约束影响及参考周期。
模型精度通过固定 workload、数值 golden 和 CAModel 时间线评估；方法见
[验证与精度](docs/validation.md)。
