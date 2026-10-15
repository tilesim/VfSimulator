# VfSimulator C++

VfSimulator 使用 C++17 对向量程序进行逐周期微架构仿真，预测指令调度、寄存器与队列资源约束、访存和屏障共同决定的执行周期，并输出事件日志及 Perfetto Trace。

输入支持 CCE/DSL 文件、Canonical JSON v1/v2 和直接构造的 `CanonicalVfInfo`。输入由 API 统一校验，Core 接收通过校验的程序。构建与运行依赖 CMake 及 C++17 编译器。

## 构建与运行

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure
build/vfsim_native_json_runner \
  --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json \
  --out-dir results/loop
```

命令行通过 `--trace` 读取 Canonical JSON，或通过 `--cce` 读取含 `__VEC_SCOPE__` 的 CCE/DSL。多 kernel 文件通过 `--cce-kernel NAME` 选择。`--out_dir`（也接受 `--out-dir`）默认使用 `results/`；相对路径以仓库根目录为基点。`--max-cycles` 可覆盖周期上限。

```bash
build/vfsim_native_json_runner --cce tests/fixtures/cce/mixed.cce --out_dir results/cce
```

CCE 前端解析声明、Catalog 指令调用、循环和访存地址，进行规范化与定义版本化，最终构造并校验 Canonical 程序。支持范围见 [输入层说明](docs/input_api.md)。理论模式使用 `--theoretical-limit-vloop-only` 和 `--theoretical-limit-vloop-only-legacy-forwarding-direct-issue`。

`vfEndCycle` 表示包含 VF drain 的结束周期；输出目录保存指令开始/完成、资源阻塞、循环、屏障和 Perfetto 事件。

## C++ 接口

```cpp
#include "api/input_api.h"
#include "core/simulator.h"

const auto vf = vfsim::InputAPI::loadJson("program.json");
vfsim::CoreVfCostModel model("/path/to/VfSimulator", "/tmp/vfsim-result");
const auto result = model.runVfInfo(vf);
const auto cycles = result.vfEndCycle;
```

`InputAPI::loadCce(path, kernelName, loopParams)` 接收 CCE 输入，`InputAPI::loadJson(path)` 接收 JSON。直接构造对象时，通过 `InputAPI::fromCanonical(vf)` 完成校验，再传给模型；`InputAPI::newBuilder()` 提供显式构造工具，`build()` 完成校验。同一模型实例可连续预测多个程序，并复用只读参数快照。每次预测具有独立的查询缓存和硬件状态。模型默认输出 `results/cost_model/`，构造时传入空路径可关闭输出。执行超时时抛出异常。

嵌入其他 CMake 项目可使用 `add_subdirectory(/path/to/VfSimulator vfsim-build)` 并链接 `vfsim::native_core`。`VFSIM_BUILD_TESTS=OFF` 关闭组件测试构建。

## 目录与文档

| 目录 | 职责 |
| --- | --- |
| `api/` | CCE 解析、Canonical 类型与 Builder、JSON 编解码、Catalog 与输入校验 |
| `core/execution/` | 程序准备、循环展开、周期推进与控制 |
| `core/uarch/` | IFU、IDU、SHQ、OoO、EXQ、EXU、LSU 与寄存器组件 |
| `core/params/` | 参数加载、快照复用、Profile 和时序查询 |
| `core/trace/` | 事件记录、结果文件与 Perfetto 导出 |
| `cli/` | Canonical JSON 命令行入口 |
| `tests/` | C++ 测试及固定 Canonical JSON 输入 |
| `configs/` | 指令语义、资源容量、latency、forwarding 和 II 配置 |

输入转换见[输入层说明](docs/input_api.md)，实现细节见 [C++ 模块说明](docs/code_structure_guide.md)、[执行模型](docs/execution_model.md)、[寄存器模型](docs/register_model.md)、[LSU 模型](docs/lsu_model.md)、[屏障时序](docs/membar_timing_model.md)和[参数配置](docs/configuration.md)。Canonical JSON 的结构定义位于 [canonical_vf_info.schema.json](api/json/canonical_vf_info.schema.json)，语义约束由输入校验器检查。
