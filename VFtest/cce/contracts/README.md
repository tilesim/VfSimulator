# CCE Canonical 合同用例

CCE 源码覆盖 alias、仿射地址、循环、谓词、对齐状态、打包访存、指针更新、转换形式、
MOVP 特殊 MASK 读取和 PLDS/PSTS 谓词保存恢复。
每份源码在 `VFtest/canonical/cce_expected/` 中有同名 JSON 预期结果。
预期数据的 source path 使用 `<cce>`，使合同比较与工作区路径无关。

`native/tests/cce_test.cpp` 比较完整 Canonical 输出；Python/C++ 对比测试同时核对执行结果和日志。
