# VfSimulator 文档

VfSimulator 预测向量函数的执行周期。Python 输入层将 CCE、Canonical JSON 和直接构造的
CanonicalVfInfo 对象统一接收，微架构组件按周期推进指令、依赖和资源状态。

## 阅读顺序

| 文档 | 内容 |
| --- | --- |
| [代码结构](code_structure_guide.md) | 目录、模块职责和关键接口 |
| [输入 API](input_api.md) | 三种输入、CCE 转换流程、Canonical 数据和校验 |
| [配置参数](configuration.md) | 配置文件、参数加载、时序查询和理论模式 |
| [执行模型](execution_model.md) | 指令准备、周期推进、循环与队列调度 |
| [寄存器模型](register_model.md) | 重命名、普通依赖、物理资源回收和对齐状态 |
| [访存模型](lsu_model.md) | LSU 仲裁、地址更新、多结果和打包访存 |
| [Membar 时序](membar_timing_model.md) | 屏障控制、方向、释放和退休 |
| [指令时序与证据](instruction_timing.md) | 参数覆盖、测量口径、近似边界及探针 |
| [验证与精度](validation.md) | 单元测试、周期回归、日志检查和精度评估 |
| [优化工具](optimization_tools.md) | 仓库内 DAG 分区、候选搜索和工具的使用前提 |

代码结构和执行模型描述 Python 实现。输入及参数的共享声明位于 `api/` 和 `configs/`；
C++ 源码位于 `api/native/` 与 `native/`，对应测试通过 Native runner 执行。
Python 稀疏检出包含输入层、仿真核心和回归所需文件。完整检出还提供优化器、校准工具
和硬件探针的配套源码。

[仓库使用入口](../README.md)提供 CLI 和库调用示例。
