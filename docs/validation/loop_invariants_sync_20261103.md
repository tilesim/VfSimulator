# 循环外提分支验证

Canonical 循环外提使用 Catalog 纯度、Scalar 定义依赖和 loop-carried 关系判断输入是否
保持不变。外提保留静态身份和出口绑定，动态展开计算共享结果的生存期。
对应实现记录为 `MultiSoC-membar-refactor:f6cdfa1`。

| 分支 | 实现与范围 | 验证结果 |
| --- | --- | --- |
| MultiSoC-membar-refactor | Python/C++，A5/A6 | 536 项 Python 测试、9 项 CTest 通过 |
| wch/VfSimulator-refactor | Python/C++，A5 | 479 项 Python 测试、8 项 CTest 通过 |
| wch/vfsim-cpp-only | C++，模块位于根目录 | 8 项 CTest 通过，Python 文件数为 0 |
| wch/VfSim-tilesim | Python 与安装包，保留 TileSim Program 接口 | 440 项及 357 个 subtest 通过，14 项可选测试跳过 |

36 组 A5 编译器探针的 Canonical 和 CCE 输入由两个 C++ runner 分别执行，预测周期和
全部移动记录与 refactor Python 相同；目标指令次数与原始 A5 测量记录相同。
探针覆盖固定/变化 mask、PLT 计数反馈、标量/向量广播、纯计算链、嵌套和零次循环。

TileSim 的 `api/`、`core/` 与 `vfsimulator/` 安装包镜像通过同步检查。
Program 接口的固定 mask 外提及 Scalar 反馈在源码与安装包中具有相同结果。
wheel 使用离线构建安装到独立目录后，在仓库外通过导入及两类预测检查。
测试使用 CPython 3.12、pytest 9.2.0 和 GCC 13.3，C++ 构建类型为 Release。

三个单架构分支的 ISA、forwarding、II、指令签名与 form 保持各自配置；Catalog 增加
纯度声明。TileSim 分支包含 Python 实现，cpp-only 分支包含 C++ 实现。

[设计与规则](../loop_invariants.md) 描述保守条件、数据字段和调用阶段。
机器对照保存在同目录的 `loop_invariants_sync_20261103.json`。
