# Master Canonical 合并验证

日期：2026-09-14。

## 合并范围

- 原 master：`5972f25`；来源分支：`vfinfo-core-api-unification`，提交 `76e739d`。
- 使用普通 merge 保留双方历史，不强制覆盖远程分支。
- `api/`、`core/`、`native/`、`configs/` 和 `main.py` 与来源分支一致。
- 保留 master 历史 baseline 和文档，更新回归默认入口到新的实测 baseline。
- 不引入局部 UB 地址依赖实验或 MultiSoC 架构。

## 验证结果

- Python 单元及跨语言测试：197/197 通过，无跳过。
- Native Release 构建及 CTest：6/6 通过。
- Python 和 Native full 回归：25/25 通过原 master 基准守卫。
- 两端 25 个 case 的完整 metrics 相同，新 baseline 与这两份实测结果严格相等，而非仅在容差内。
- Membar 单独验证：single=114、clustered8=338、interleaved8=348、spill=396 cycles；
  两端 start/done 与 barrier 事件逐项一致。
- 未重新运行 CAmodel，CCE 参考值沿用回归 manifest。
- 本次未放宽任何回归容差。

## 历史基准差异

| Case | 原 master 基准 | 当前 Python/C++ | 差值 |
| --- | ---: | ---: | ---: |
| online_update_i64_u1 | 438 | 434 | -4 |
| gelu_poly_i96_u6 | 1731 | 1746 | +15 |
| gelu_poly_i96_u8 | 1706 | 1717 | +11 |

这三项是此前新分支已有的变化，不是本次 merge 新引入；合并没有改动来源分支的核心代码。
完整回归输入均无 membar，不能将这些差异归因于新增 membar 时序。
online_update 相对 CCE 的绝对误差为 5→9 cycles，GeLU U8 为 12→1 cycles；
U6 在当前 manifest 中没有 CCE 参考值，不对其精度提升作结论。

保留 `baseline_balanced_exu0_reserve.json` 不变，新结果存入
`../cases/baseline_canonical_membar.json` 并设为默认。
这个 baseline 是当前行为快照，不代表所有 case 对真实硬件都达到高精度。

## 完整周期表

| Case | Python | Native |
| --- | ---: | ---: |
| gelu_poly_i16_u1 | 404 | 404 |
| gelu_poly_i64_u1 | 1319 | 1319 |
| gelu_poly_i96_u1 | 1928 | 1928 |
| gelu_i16_u1 | 189 | 189 |
| online_update_i64_u1 | 434 | 434 |
| probe_src_fanout | 282 | 282 |
| probe_branch_live_range | 218 | 218 |
| probe_store_capture_reuse | 317 | 317 |
| vadds_longchain_i16_1x512 | 22153 | 22153 |
| vadds_longchain_i64_8x64 | 39536 | 39536 |
| vadds_longchain_i128_128x4 | 32827 | 32827 |
| vexp_longchain_i16_1x512 | 95492 | 95492 |
| vexp_longchain_i64_8x64 | 161198 | 161198 |
| vexp_longchain_i128_128x4 | 131137 | 131137 |
| gelu_poly_i96_u2 | 1856 | 1856 |
| gelu_poly_i96_u3 | 1807 | 1807 |
| gelu_poly_i96_u4 | 1777 | 1777 |
| gelu_poly_i96_u6 | 1746 | 1746 |
| gelu_poly_i96_u8 | 1717 | 1717 |
| silu_i16_u1 | 154 | 154 |
| swiglu_i16_u1 | 172 | 172 |
| silu_i64_u1 | 411 | 411 |
| silu_i96_u1 | 582 | 582 |
| swiglu_i64_u1 | 469 | 469 |
| swiglu_i96_u1 | 661 | 661 |

## 复现

```bash
cmake -S native -B /tmp/vfsim_master -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/vfsim_master -j 4
ctest --test-dir /tmp/vfsim_master --output-on-failure
VFSIM_NATIVE_RUNNER=/tmp/vfsim_master/vfsim_native_json_runner python3 -m unittest discover -s tests
python3 tools/run_cost_model_regression.py --tier full --out-dir /tmp/master-python
python3 tools/run_native_cost_model_regression.py --tier full --runner /tmp/vfsim_master/vfsim_native_json_runner --out-dir /tmp/master-native
python3 tools/validate_global_membar.py --out-dir /tmp/master-membar --native-runner /tmp/vfsim_master/vfsim_native_json_runner
```

本次原始日志位于本机 `/tmp/master-merge-regression/`，不纳入 Git；
可检出的输入、模型参数、baseline 和验证工具足以重新运行。
