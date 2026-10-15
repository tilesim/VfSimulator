# VfSimulator C++

VfSimulator 使用 C++17 对向量程序进行逐周期微架构仿真，预测指令调度、寄存器与队列资源约束、访存和屏障共同决定的执行周期，并输出事件日志及 Perfetto Trace。

输入支持 Canonical JSON v1/v2 和直接构造的 `CanonicalVfInfo`。输入由 API 统一校验，Core 接收通过校验的程序。构建与运行依赖 CMake 及 C++17 编译器。

## 构建与运行

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
build/native/vfsim_native_json_runner \
  --trace native/tests/fixtures/canonical_vf_info/v1_valid_loop.json \
  --out-dir results/loop
```

命令行接收 `--trace`、`--out-dir` 和可选的 `--max-cycles`。`--trace` 指定 Canonical JSON 文件。其他程序格式需在调用前转换成 Canonical 输入。

`vfEndCycle` 表示包含 VF drain 的结束周期；输出目录保存指令开始/完成、资源阻塞、循环、屏障和 Perfetto 事件。

## C++ 接口

```cpp
#include "native/api/input_api.h"
#include "native/core/simulator.h"

const auto vf = vfsim::InputAPI::loadJson("program.json");
vfsim::CoreVfCostModel model("/path/to/VfSimulator", "/tmp/vfsim-result");
const auto result = model.runVfInfo(vf);
const auto cycles = result.vfEndCycle;
```

直接构造对象时，通过 `InputAPI::fromCanonical(vf)` 完成校验，再传给模型。同一模型实例可连续预测多个程序，并复用只读参数快照。每次预测具有独立的查询缓存和硬件状态。

嵌入其他 CMake 项目可使用 `add_subdirectory(native)` 并链接 `vfsim::native_core`。`VFSIM_BUILD_TESTS=OFF` 关闭组件测试构建。

## 目录与文档

| 目录 | 职责 |
| --- | --- |
| `native/api/` | Canonical 类型、JSON 解码、指令 Catalog 与输入校验 |
| `native/core/execution/` | 程序准备、循环展开、周期推进与控制 |
| `native/core/uarch/` | IFU、IDU、SHQ、OoO、EXQ、EXU、LSU 与寄存器组件 |
| `native/core/params/` | 参数加载、快照复用、Profile 和时序查询 |
| `native/core/trace/` | 事件记录、结果文件与 Perfetto 导出 |
| `native/cli/` | Canonical JSON 命令行入口 |
| `native/tests/` | C++ 测试及固定 Canonical JSON 输入 |
| `configs/` | 指令语义、资源容量、latency、forwarding 和 II 配置 |

实现细节见 [C++ 模块说明](native/README.md)、[执行模型](docs/execution_model.md)、[寄存器模型](docs/register_model.md)、[LSU 模型](docs/lsu_model.md)、[屏障时序](docs/membar_timing_model.md)和[参数配置](docs/configuration.md)。Canonical JSON 的结构定义位于 [canonical_vf_info.schema.json](native/api/json/canonical_vf_info.schema.json)，语义约束由输入校验器检查。
