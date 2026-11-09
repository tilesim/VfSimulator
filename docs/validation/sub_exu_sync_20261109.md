# 子 EXU 模型跨分支验证

共享参考版本为 `wch/VfSimulator-refactor` 的 `97c1e12`，覆盖子单元接收状态、II 例外、
COMPUTE→STORE forwarding 与参数化推导系数。

| 对象 | 验证结果 |
| --- | --- |
| TileSim Python | 475 项测试、1943 个子测试通过 |
| 源码与包副本 | 71 份生成文件检查一致 |
| 纯 C++ | 8 项 CTest 通过，C++ 核心按目录路径转换后与参考实现一致 |
| 独立 wheel | 在仓库外安装，使用隔离 Python 进程加载包内代码和参数 |
| 完整算例 | 48 个输入的执行周期、VF 结束周期和静态指令条目数与 C++、参考记录一致 |

TileSim 保留 typed program 接口、Canonical 转换和预测结果结构。参数缓存跟踪 ISA、
uarch、forwarding、II、资源规则和例外表的文件版本；更新配置后使用新的模型快照。
C++ 分支的输入层、核心、命令行及测试位于主目录，构建使用 CMake 和 C++17。

[机器记录](sub_exu_sync_20261109.json) 保存配置摘要、wheel 摘要和逐输入对照结果。
