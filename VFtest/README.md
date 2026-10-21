# 测试案例

Python 与 C++ 实现共享本目录中的固定输入，自动化测试代码分别位于 `tests/` 和 `native/tests/`。

| 目录 | 内容 |
| --- | --- |
| `cce/` | CCE 源码，统一使用 `.cce` 后缀；包括算子、软硬件探针、屏障和指令合同用例 |
| `canonical/` | Canonical 标准数据、校验与时序 fixture，以及 CCE lowering 的预期结果 |
| `json/` | 供 JSON 文件入口加载的算子和依赖探针输入 |

`canonical/` 和 `json/` 均采用 API 定义的 Canonical JSON 合同。JSON 入口负责反序列化和校验，
程序化输入可以由 Builder 构造，或将 Canonical 数据反序列化后交给 `InputAPI.from_canonical()`。

`canonical/fixtures/` 保存固定行为测试，包含明确标注的非法输入；`canonical/cce_expected/` 的
JSON 与 `cce/contracts/` 中同名 CCE 配对，用于检查完整的 Canonical 输出。
`common_instruction_pairs_fp32/fp16/bf16.cce` 由跨指令时序测试加载，
检查混合计算、比较与选择链及 Python/C++ 发射周期。
`cce/contracts/vector_conversions.cce` 与 `vector_conversion_views.cce` 覆盖转换签名、
64-bit 类型、打包与显式视图，配对 Canonical 文件固定其依赖合同。
`cce/membar_timing/` 和 `cce/ordinary_instructions/` 分别保存屏障及指令覆盖案例。

在仓库根目录运行：

```bash
python3 main.py --cce VFtest/cce/GeLU_poly.cce --out_dir results/gelu_cce
python3 main.py --trace VFtest/json/GeLU_poly.json --out_dir results/gelu_json
python3 main.py --trace VFtest/canonical/fixtures/v1_valid_loop.json --out_dir results/fixture
```

指令参数实验工具位于 `skills/vfsim-instruction-benchmark/`。运行时生成的临时源码、日志与二进制
保存在独立运行目录；需长期复用的固定输入按上述类别归档。既有 CAModel 运行证据位于
`regression_suite/evidence/cce/`，host 模板位于标定 skill 的 `assets/`。
