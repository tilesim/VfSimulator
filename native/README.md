# C++ 微架构仿真器

C++17 实现使用 CanonicalVfInfo 描述程序，并逐 cycle 推进微架构组件。
`api/native/input_api.h` 提供输入接收接口，`native/simulator.h` 提供周期预测接口。
Python 与 C++ 共享 Canonical JSON、指令 Catalog、参数配置和回归用例。

## 输入与调用

`InputAPI::loadJson(path)` 解码并校验 Canonical JSON v1/v2。
`InputAPI::fromCanonical(vf)` 校验直接构造的 CanonicalVfInfo，返回接收成功的对象。
两种输入在 API 出口完成通用语义校验。
`CoreVfCostModel::runVfInfo()` 接收校验成功的对象，完成配置解析、执行准备和仿真。
CCE 文件通过 Python InputAPI 前端转换为 Canonical JSON，供 C++ 输入层接收。

```cpp
#include "api/native/input_api.h"
#include "native/simulator.h"

const auto vf = vfsim::InputAPI::loadJson("program.json");
vfsim::CoreVfCostModel simulator("/path/to/VfSimulator", "/tmp/vfsim-result");
const auto result = simulator.runVfInfo(vf);
const auto cycles = result.vfEndCycle;
```

Core 的执行准备检查显式依赖、控制指令和循环展开等执行能力。
存储类别由 CanonicalValue 的 storage 字段确定。
`canonical_dynamic_instruction_limit` 默认限制展开后的指令数量为 20,000；
值为 0 时关闭限制，适用于显式控制规模的回归程序。

## 目录职责

| 目录或文件 | 功能 |
| --- | --- |
| `simulator.h/.cpp` | 公共仿真接口、配置组合和结果返回 |
| `execution/instruction.h` | 动态指令、重命名后的 Uop 和内部 value 信息 |
| `execution/prepare.*` | 执行能力检查、内部 value 表准备 |
| `execution/expansion.*` | 循环展开、动态定义身份、末次使用和循环边界标注 |
| `execution/engine.*` | 全局 cycle、传输延迟、分派预算、组件协作和排空 |
| `execution/loop_control.*` | VLOOP 启动、循环反馈与分派开放时间 |
| `execution/membar.*` | 屏障接收、同步释放、消费者门控和控制完成 |
| `uarch/ifu.h` | 已准备的指令流 |
| `uarch/idu.*` | 有序分派、资源预算和地址更新依赖 |
| `uarch/register_bank.h`、`registers.*` | 物理寄存器、重命名、源释放、可见信用和对齐状态 |
| `uarch/shq.h` | 计算等待队列及共享 SHQ 信用 |
| `uarch/ooo.*` | 就绪指令选择、SHQ 到 EXQ 的端口分配和直接发射 |
| `uarch/exq.*` | 各端口的 FU 队列、接收时间和队首选择 |
| `uarch/exu.*` | EXU 端口约束、II、运行中指令和完成 |
| `uarch/lsu.*` | LSQ、访存依赖、load/store 优先级及共享 UB 预算 |
| `params/` | 参数类型、文件读取、配置解析、指令查询和缺失项回退 |
| `trace/` | 事件快照、结果类型、日志及 Perfetto Trace 写出 |
| `cli/` | Canonical JSON 命令行工具 |
| `tests/` | 输入契约、组件时序、资源、地址、谓词和 membar 测试 |

组件持有各自的队列和运行状态；Engine 管理组件之间的时序。
SHQ、EXQ、EXU、LSU 和完成跟踪共用同一 Uop 对象。
Engine 的活跃指令集合用于完成检查与排空。

`api/native/common/` 定义语义类型、Catalog、诊断和校验；
`api/native/json/` 负责 JSON 解析及 Canonical 解码；
`api/native/generated/` 存放共享配置生成的数据表。

## 周期推进

每个 cycle 依次更新可见信用、接收传输中的指令、补充 IDU、更新屏障并执行有序分派。
后端随后处理源与 SHQ 信用释放、执行完成、就绪状态、LSU 发射和 OoO 发射。
LSU 的两次发射阶段共享当周期的端口、事务槽和字节预算。
执行完成与屏障完成共同决定 VF 排空时间，ISA 的 drain cost 加入 vfEndCycle。

## 构建与验证

```bash
cmake -S native -B build-native -DCMAKE_BUILD_TYPE=Release -DVFSIM_BUILD_TESTS=ON
cmake --build build-native -j4
ctest --test-dir build-native --output-on-failure
build-native/vfsim_native_json_runner \
  --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json \
  --out-dir /tmp/vfsim-native
VFSIM_NATIVE_RUNNER="$PWD/build-native/vfsim_native_json_runner" \
  python3 -m unittest discover -s tests
```

库目标为 `vfsim::native_core`，可通过 `add_subdirectory(native)` 引入。
JSON runner 独立于测试开关构建。`--max-cycles` 可覆盖程序的周期上限。
参数文件从 baseDir 下的 configs/ 读取；环境变量路径选择与 Python params/ 一致。

结果包括 cyclesExecuted、vfEndCycle、linearInstCount、resultsDir 和 tracePath。
linearInstCount 统计静态线性布局条目，包含循环首尾控制条目。
输出目录包含 sim_history.json、start_by_cycle.json、done_by_cycle.json、
idu_to_ooo.json、地址与资源阻塞日志、vloop_trace.json 和 trace.json。
启用 membar timing 时输出 membar_history.json；发生参数回退时输出 model_warnings.json。
开始和完成事件使用静态指令 ID、迭代路径和 stream_seq 关联到 Canonical 程序。
