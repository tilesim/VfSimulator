# TileSim Python 同步验证

验证完成日期为 2026-10-22，工作分支为 `wch/VfSim-tilesim`。
分支基点为 `tilesim/VfSim-tilesim` 的 `8dafdd0`，Python 模型来源为
[refactor 的 4cc2015](https://github.com/tilesim/VfSimulator/commit/4cc20155f36be92e24772e4909bd7b3148c6e389)。

## 代码和参数

`api/`、`core/` 与 `configs/` 中的 62 个共享文件逐字节一致；其中 Python 模块为 52 个。
`api/tilesim/` 保存本分支的程序对象、Catalog 绑定、Canonical 转换及预测接口。
Core 使用 execution、uarch、params、trace 的组件结构。
代码及固定输入使用 Python、JSON 和 CCE，分支不包含 C++ 实现和头文件。

安装包从源代码生成命名空间导入，`sync_python_package.py --check` 核对 66 个生成文件。
wheel 内的 65 个运行文件与工作区一致，包含 7 份配置和 Canonical JSON schema。
包版本为 `0.4.0`，wheel SHA256 为
`2e1bfdbe86e31ccba895fa42278508815719d7217cb6d7b3339975d8e25d6c64`。

## 模型与安装包验证

| 检查 | 结果 |
| --- | --- |
| Python 测试 | 417 passed、13 skipped、328 subtests passed |
| 独立 wheel 安装 | 通过 |
| 仓库外命名空间隔离 | 未导入顶层 api/core |
| CCE 软件展开用例 | VF end cycle 为 94 |
| 固定完整算子和依赖探针 | 37 / 37 与 refactor 周期一致 |
| 发射及完成事件 | 37 / 37 的 `(inst_id, op, cy)` 数量与 SHA256 一致 |

用例覆盖 GeLU、SiLU、SwiGLU、Softmax、在线更新、长指令链和依赖探针。
13 项跳过项需要匹配的外部 Native runner；本分支的 Python 模型和对接测试独立运行。
配置缓存用例检查首次 4 次参数读取、重复调用零次重读，以及文件更新后的重新加载。

## TileSim 端验证

本机 TileSim 源码提交为 `e7d9d535eb944b2810a3922e46bbfbba1a8104e3`。
验证使用其临时副本与独立 wheel 安装目录，原 TileSim 工作区和所用安装环境保持原状。
测试范围为 `tests/ut/core/backend/vf_costmodel`、A5 ADD 和 DSL→MIR→工程仿真。

原始 TileSim 测试结果为 79 passed、3 failed：

- 两项双结果 LOAD 测试断言 `span=128`。当前 Canonical 通过 `span=512` 与
  `address_unit_bytes=1` 表达相同的 512B；调用方的元素偏移语义由 adapter 保留。
- 一项完整 Catalog 枚举测试发现 TileSim 缺少指令名称。补丁补充 159 个枚举项及其类别。

[下游适配补丁](../tilesim_compatibility/catalog_and_memory_units.patch)修改 TileSim 的
`core/common/cce_vinst.py` 和相应的访存测试断言。补丁已通过原仓库的 `git apply --check`。
临时副本应用补丁后的同一集合为 **82 passed**。该补丁随本分支保存，应用到 TileSim 仓库的工作独立进行。

[机器报告](tilesim_python_sync_20261021.json)保存共享文件、配置及事件哈希，
逐例周期和两组 TileSim 验证状态。原始日志位于独立验证目录。
