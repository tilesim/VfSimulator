# CCE Canonical 合同用例

CCE 源码覆盖 alias、仿射地址、循环、谓词、对齐状态、打包访存、指针更新、转换形式、
MOVP 特殊 MASK 读取和 PLDS/PSTS 谓词保存恢复。
每份源码在 `VFtest/canonical/cce_expected/` 中有同名 JSON 预期结果。
预期数据的 source path 使用 `<cce>`，使合同比较与工作区路径无关。

Python 测试比较 Canonical 结果；双实现对照记录可在共享模型分支查看。
