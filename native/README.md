# C++ 微架构仿真器

C++17 实现使用 CanonicalVfInfo 描述程序，并逐 cycle 推进微架构组件。
`native/api/input_api.h` 提供输入接收接口，`native/core/simulator.h` 提供周期预测接口。
Python 与 C++ 共享 Canonical JSON、指令 Catalog、参数配置和回归用例。

## 输入与调用

`InputAPI::loadJson(path)` 解码并校验 Canonical JSON v1/v2。
`InputAPI::fromCanonical(vf)` 校验直接构造的 CanonicalVfInfo，返回接收成功的对象。
两种输入在 API 出口完成通用语义校验。
`CoreVfCostModel` 构造时加载参数快照，`runVfInfo()` 接收校验成功的对象，
合并 VF 配置覆盖、准备指令并运行仿真。
`InputAPI::loadCce(path, kernelName, loopParams)` 独立解析 CCE/DSL，进行规范化和值版本化，并在 API 出口校验。
`InputAPI::newBuilder(params, uarch, source)` 返回显式 Canonical Builder，支持值/存储声明、指令、屏障和循环作用域。
`encodeCanonicalVfInfoJson()` 与 `decodeCanonicalVfInfoJson()` 提供完整 Canonical JSON 往返转换。

```cpp
#include "native/api/input_api.h"
#include "native/core/simulator.h"

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

```text
native/
  CMakeLists.txt
  api/
    input_api.h/.cpp
    cce/             CCE 扫描、Catalog 调用绑定、规范化和定义版本化
    programmatic/    Canonical Builder 及循环作用域
    common/          Canonical、Catalog、诊断与校验
    json/            JSON 解析与 Canonical 编解码
    generated/       共享配置生成的数据表
  core/
    simulator.h/.cpp
    execution/       指令准备、展开、Engine、loop 与 membar
    uarch/           硬件组件与状态转换
    params/          参数快照、Profile、时序查询与回退
    trace/           事件记录、结果写出与 Perfetto 导出
  cli/               命令行入口
  tests/             C++ 组件与契约测试
```


| 目录或文件 | 功能 |
| --- | --- |
| `core/simulator.h/.cpp` | 公共仿真接口、配置组合和结果返回 |
| `core/execution/instruction.h` | 动态指令、重命名后的 Uop 和内部 value 信息 |
| `core/execution/prepare.*` | 执行能力检查、内部 value 表准备 |
| `core/execution/expansion.*` | 循环展开、动态定义身份、末次使用和循环边界标注 |
| `core/execution/engine.*` | 全局 cycle、传输延迟、分派预算、组件协作和排空 |
| `core/execution/loop_control.*` | VLOOP 启动、循环反馈与分派开放时间 |
| `core/execution/membar.*` | 屏障接收、同步释放、消费者门控和控制完成 |
| `core/uarch/ifu.h` | 已准备的指令流 |
| `core/uarch/idu.*` | 有序分派、资源预算和地址更新依赖 |
| `core/uarch/register_bank.h`、`core/uarch/registers.*` | 物理寄存器、重命名、源释放、可见信用和对齐状态 |
| `core/uarch/shq.h` | 计算等待队列及共享 SHQ 信用 |
| `core/uarch/ooo.*` | 就绪指令选择、SHQ 到 EXQ 的端口分配和直接发射 |
| `core/uarch/exq.*` | 各端口的 FU 队列、接收时间和队首选择 |
| `core/uarch/exu.*` | EXU 端口约束、II、运行中指令和完成 |
| `core/uarch/lsu.*` | LSQ、访存依赖、load/store 优先级及共享 UB 预算 |
| `core/params/` | 参数类型、文件读取、配置解析、指令查询和缺失项回退 |
| `core/trace/` | 事件快照、结果类型、日志及 Perfetto Trace 写出 |
| `cli/` | CCE / Canonical JSON 命令行工具 |
| `tests/` | 输入契约、组件时序、资源、地址、谓词和 membar 测试 |

组件持有各自的队列和运行状态；Engine 管理组件之间的时序。
SHQ、EXQ、EXU、LSU 和完成跟踪共用同一 Uop 对象。
Engine 的活跃指令集合用于完成检查与排空。
Uop 引用本次运行的 ParamDB 持有的只读 InstructionProfile；指令类别、latency、FU 和
允许端口由 Profile 提供。ParamDB 的生命周期覆盖 Engine、各组件和 Trace 写出。

`api/common/validator.*` 校验定义、作用域、循环、访存和依赖；
`api/common/instruction_validation.*` 校验 Catalog 的指令形式和操作数。
`core/trace/recorder.*` 记录事件，`writer.*` 写出结果和事件文件，
`perfetto.*` 将事件导出为 Perfetto JSON。

`native/api/common/` 定义语义类型、Catalog、诊断和校验；
`native/api/json/` 负责 JSON 解析及 Canonical 解码；
`native/api/generated/` 存放共享配置生成的数据表。

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
同一模型的连续运行共享只读 `ParamBundle`，配置文件和路径环境变量的更新在创建
新的模型实例后生效。每次运行创建独立的 `ParamDB`、查询缓存、警告和微架构状态。
forwarding 按 producer opcode/form 与 consumer opcode/form 缓存相对周期数。

结果包括 cyclesExecuted、vfEndCycle、linearInstCount、resultsDir 和 tracePath。
linearInstCount 统计静态线性布局条目，包含循环首尾控制条目。
输出目录包含 sim_history.json、start_by_cycle.json、done_by_cycle.json、
idu_to_ooo.json、地址与资源阻塞日志、vloop_trace.json 和 trace.json。
启用 membar timing 时输出 membar_history.json；发生参数回退时输出 model_warnings.json。
开始和完成事件使用静态指令 ID、迭代路径和 stream_seq 关联到 Canonical 程序。

C++ 命令行通过 `--cce` / `--trace` 选择输入，通过 `--cce-kernel` 选择 VF kernel。
`--out_dir` 和 `--out-dir` 指定结果目录，默认 `results/`。
两个 theoretical-limit 开关与 Python 主入口对应。
库模型默认输出 `results/cost_model/`，传入空输出路径可关闭文件写出；超时抛出异常。
开始和完成日志包含 `fu_type`、源/目的动态值实例及完整 induction 迭代路径。
Perfetto 输出进程、线程元数据和实际开始/完成区间。警告 JSON 保留标量及嵌套对象类型。
