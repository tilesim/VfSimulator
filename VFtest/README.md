# 测试案例

本目录保存固定输入与 Canonical 预期结果，C++ 自动化测试代码位于 `tests/`。

| 目录 | 内容 |
| --- | --- |
| `cce/` | CCE 源码，统一使用 `.cce` 后缀；包括算子、软硬件探针、屏障和指令合同用例 |
| `canonical/` | Canonical 标准数据、校验与时序 fixture，以及 CCE lowering 的预期结果 |
| `json/` | 供 JSON 文件入口加载的算子和依赖探针输入 |

`canonical/` 和 `json/` 均采用 API 定义的 Canonical JSON 合同。JSON 入口负责反序列化和校验，
程序化输入可以由 Builder 构造，或将 Canonical 数据反序列化后交给 `InputAPI::fromCanonical()`。

`canonical/fixtures/` 保存固定行为测试，包含明确标注的非法输入；`canonical/cce_expected/` 的
JSON 与 `cce/contracts/` 中同名 CCE 配对，用于检查完整的 Canonical 输出。
`common_instruction_pairs_fp32/fp16/bf16.cce` 由跨指令时序测试加载，
检查混合计算、比较与选择链及发射周期。
`cce/contracts/vector_conversions.cce` 与 `vector_conversion_views.cce` 覆盖转换签名、
64-bit 类型、打包与显式视图，配对 Canonical 文件固定其依赖合同。
`cce/membar_timing/` 和 `cce/ordinary_instructions/` 分别保存屏障及指令覆盖案例。

在仓库根目录运行：

```bash
build/vfsim_native_json_runner --cce VFtest/cce/GeLU_poly.cce --out_dir results/gelu_cce
build/vfsim_native_json_runner --trace VFtest/json/GeLU_poly.json --out_dir results/gelu_json
build/vfsim_native_json_runner --trace VFtest/canonical/fixtures/v1_valid_loop.json --out_dir results/fixture
```

指令参数实验工具与证据位于
[refactor 分支的标定 skill](https://github.com/tilesim/VfSimulator/tree/wch/VfSimulator-refactor/skills/vfsim-instruction-benchmark)。
运行时生成的临时源码、日志与二进制保存在独立的 `results/` 运行目录。
需长期复用的固定输入按上述类别归档。
