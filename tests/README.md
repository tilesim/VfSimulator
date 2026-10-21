# C++ 测试

通过 CTest 运行输入、硬件组件与时序测试：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DVFSIM_BUILD_TESTS=ON
cmake --build build -j4
ctest --test-dir build --output-on-failure
```

| 测试 | 内容 |
| --- | --- |
| `input_api_test.cpp` | Canonical JSON、Catalog、定义与作用域校验 |
| `cce_test.cpp` | CCE 解析、类型与软件展开、完整 Canonical 预期结果 |
| `components_test.cpp` | OoO、EXQ、EXU、self-II 历史和完成周期预留 |
| `parameters_test.cpp` | 参数加载、快照复用、查询及缺失项回退 |
| `predicate_register_test.cpp` | 谓词重命名、依赖与寄存器资源 |
| `address_state_test.cpp` | 地址更新与访存依赖 |
| `membar_timing_test.cpp` | 屏障接收、门控和时序 |
| `programmatic_test.cpp` | Builder、循环作用域和对象输入 |

固定输入位于 `VFtest/cce/`、`VFtest/canonical/` 与 `VFtest/json/`。
构建直接使用 C++ 源码和 `api/generated/` 数据表。
