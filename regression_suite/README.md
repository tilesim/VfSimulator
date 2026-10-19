# 回归测试与测量证据

本目录保存回归清单、周期基线、精度报告及 CAModel 运行证据。固定输入统一位于 `VFtest/`。

| 路径 | 内容 |
| --- | --- |
| `cases/cost_model_regression_cases.json` | case ID、输入路径、参数覆盖、参考周期和容差 |
| `cases/baseline_canonical_membar.json` | 当前默认周期基线 |
| `cases/archive/` | 历史实验清单及基线 |
| `evidence/cce/` | 对应 CCE 实验的原始日志、输出及二进制证据 |
| `reports/` | 精度与验证结果报告 |
| `docs/` | 回归分析方法 |

主清单引用 `VFtest/canonical/` 的输入。CCE 源码位于 `VFtest/cce/`，
JSON 文件入口案例位于 `VFtest/json/`。共享案例供 Python/C++ 使用。

在仓库根目录运行 Python 回归：

```bash
python3 tools/run_cost_model_regression.py --tier smoke
python3 tools/run_cost_model_regression.py --tier full
```

构建并运行 C++ 回归：

```bash
cmake -S native -B build-native -DVFSIM_BUILD_TESTS=ON
cmake --build build-native -j2
python3 tools/run_native_cost_model_regression.py --tier smoke
```

脚本生成本次 metrics 和对比报告。确认参数、执行行为和精度变化后，
可按任务要求通过 `--update-baseline` 保存新的参考结果。
