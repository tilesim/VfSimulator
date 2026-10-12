# VF Simulator

VF Simulator 是面向 Ascend 风格 VF（Vector Function）的周期级性能模型。它根据动态指令依赖、队列容量、物理寄存器、执行端口、forwarding、II、访存端口和 membar 时序，预测一个 VF 从启动到流水线排空所需的周期。

模型不执行算子数值计算。`vf_end_cycle` 用于性能预测；数值正确性仍需通过 CAmodel、设备运行和 golden 数据验证。

当前正式输入合同是 `CanonicalVfInfo`，支持 schema v1/v2。CCE/DSL、Canonical JSON 和程序化 Builder 只是不同前端，最终进入同一套 Core 语义。

## 快速开始

运行包含 `__VEC_SCOPE__` 的 CCE/DSL：

```bash
python3 main.py \
  --cce cce_code/GeLU_poly.dsl \
  --out_dir results/demo_gelu_poly
```

一个文件包含多个 VF kernel 时指定名称：

```bash
python3 main.py \
  --cce path/to/kernel.cce \
  --cce-kernel vf_kernel_name \
  --out_dir results/demo_kernel
```

运行 Canonical JSON：

```bash
python3 main.py \
  --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json \
  --out_dir results/demo_json
```

`--trace` 在这里表示 Canonical 输入 JSON，不是模型运行后生成的 Perfetto trace。`--trace` 和 `--cce` 不能同时使用；不传两者时会运行默认 fixture。

终端输出中的主要结果是：

```text
VF end cycle (with drain) = ...
```

它包含 startup、指令执行、membar 退休和尾部 drain，不等同于最后一条 compute 的开始或完成周期。

## 整体架构

```text
CCE / DSL               Canonical JSON             VfInfoBuilder
   │                          │                          │
   ├─ CCE scope parser       ├─ JSON Schema 校验        ├─ 程序化构造
   └─ ValueVersioning        └─ 对象解码                └─ build()
                 \             │             /
                       CanonicalVfInfo v1/v2
                   通用校验 + Catalog 语义校验
                               │
                       CoreLoweringPass
             program / values / params / uarch payload
                               │
               execution/prepare + expansion + ParamDB
                               │
                    execution/engine → uarch components
                               │
              ┌────────────────┼─────────────────┐
              │                │                 │
       SHQ → EXQ → EXU        LSQ        execution/membar
          compute path     load/store path       control path
              │                │                 │
              └────────────────┼─────────────────┘
                               │
                       Engine.run()
                         逐周期推进模型
                               │
       vf_end_cycle / JSON 日志 / Perfetto trace / warnings
```

### 1. 输入与 Canonical 前端

公共输入入口在 `api/input_api.py`：

- `InputAPI.load_cce()`：解析 CCE/DSL 中受支持的 VF 语句。
- `InputAPI.load_json()`：严格读取 Canonical JSON。
- `InputAPI.new_builder()`：由 Python 代码直接构造 Canonical 输入。

`CanonicalVfInfo` 定义在 `api/common/schema.py`，主要描述：

- instruction、loop 和独立的 membar 控制节点；
- 每一次 register/scalar/UB value definition；
- loop entry、back-edge 和 exit value；
- 稳定的 UB storage object 与访存描述；
- 静态依赖、参数、源码位置及可选微架构覆盖。

CCE 解析器不是完整 C++ 编译器，只解析模型明确支持的 `__VEC_SCOPE__` 内容。host 代码、GM 搬运和完整标量程序不属于 VF Core 预测范围。

#### CanonicalVfInfo 核心定义

Python 定义位于 `api/common/schema.py`，C++ 等价定义位于
`api/native/CanonicalVfInfo.h`，跨语言 JSON 合同位于
`api/json/canonical_vf_info.schema.json`。

```python
@dataclass(frozen=True)
class CanonicalVfInfo:
    context: tuple[CanonicalNode, ...]
    values: Mapping[str, CanonicalValue]
    storage_objects: Mapping[str, CanonicalStorageObject]
    params: Mapping[str, int]
    uarch: Mapping[str, ScalarValue]
    source: Mapping[str, ScalarValue]
    schema_version: int = 1
```

核心结构：

| 结构 | 含义 |
|---|---|
| `CanonicalNode` | `CanonicalInstruction`、`CanonicalLoop` 或 `CanonicalMembar` |
| `CanonicalInstruction` | opcode、form、class，以及引用 value 的 inputs/outputs |
| `CanonicalValue` | 一次具体的 SSA/value definition，记录 logical ID、producer、storage 和 dtype |
| `CanonicalOperand` | 通过 `value_id` 引用 value，并记录 source/destination/memory 等角色 |
| `CanonicalStorageObject` | 稳定的 UB/存储对象身份，不等同于一次 value definition |
| `CanonicalLoop` | 循环、迭代变量、body，以及 entry/back-edge/exit carried value |
| `CanonicalMembar` | 独立内存同步节点，不伪装成 compute 或 LSU 指令 |

最核心的关系是：

```text
CanonicalVfInfo
│
├── context
│   ├── CanonicalInstruction
│   │      inputs/outputs ──────┐
│   ├── CanonicalLoop           │ value_id
│   │      body: CanonicalNode  │
│   └── CanonicalMembar         │
│                              ▼
├── values[definition_id] = CanonicalValue
│                              │
│                              ├── producer_node_id
│                              └── storage_object_id
│                                           │
│                                           ▼
└── storage_objects[object_id] = CanonicalStorageObject
```

`context` 描述程序顺序和控制结构；`values` 描述每一次数据定义及其 producer；
`storage_objects` 描述稳定存储对象。普通寄存器数据依赖由 operand 的 `value_id`
连接到对应 `CanonicalValue`，不依赖逻辑寄存器名称或静态 PC 猜测。

### 2. 执行准备与动态展开

`core/execution/prepare.py` 接收 API 已校验的 Canonical，执行 Core 能力检查、表示转换和静态循环分析；
`core/execution/expansion.py` 展开循环、unroll 和 loop-carried 绑定，标记动态值的最后使用。
动态值身份是 `(definition_id, iteration_path)`，不通过变量名或 `_laneN` 后缀推断。
`canonical_dynamic_instruction_limit` 默认 20000，可显式配置；`0` 表示关闭限制。

### 3. 执行控制与硬件状态

`core/simulator.py` 是唯一公开 Core 入口，`CoreVfCostModel.run_vf_info()` 只接收
API 已校验的 `CanonicalVfInfo`。`main.py` 先通过 `InputAPI` 读取文件，再调用这一入口。
直接构造的对象通过 `InputAPI.from_canonical()` 接收并校验；CCE/JSON/Builder 的输出已校验，
可以直接进入 Core。Core 不再重复做通用语义校验。

```text
CanonicalVfInfo → execution/prepare + expansion → PreparedProgram
                                                   │
                         execution/engine 逐周期推进
                                                   │
       IFU → IDU → registers → SHQ → EXQ → EXU      │ compute
                            └──→ LSU (LSQ)          │ load/store
       membar → execution/membar                   │ control
```

- `execution/engine.py`：维护全局周期、传输管线、组件完成事件和排空条件。
- `execution/scheduler.py`：选择 SHQ→EXQ 端口和 EXQ→EXU 发射顺序。
- `execution/loop_control.py`：维护 VLOOP、循环迭代及顶层 block 的开放时刻。
- `execution/membar.py`：维护 barrier admission、同步、release、retire 和消费者门控。
- `uarch/`：分别拥有 IFU、IDU、register banks、SHQ、EXQ、EXU 和 LSU 的状态。
- `common/`：动态指令、Uop、结果类型及按显式声明查询存储类别的代码。
- `params/`：loader 读取配置，types 定义结构化参数，ParamDB 查询与缓存，uarch 合并架构配置，fallback 处理缺失参数。
- `trace/`：记录并写出执行日志及 Perfetto 文件。

寄存器在最后一个 consumer 开始后经过配置延迟，并满足映射、pending producer、
consumer 数量和 generation 条件时归还。LOAD/STORE 使用 LSU 内部 LSQ 和共享 UB
issue/bandwidth 预算；compute 使用 SHQ→EXQ→EXU。默认保留原有 ALU/SFU 分别
round-robin 和 EXU0-only 预留策略。

Core 已删除 ROB 容量及按序退休逻辑。EXU、LSU 分别产生完成事件；Engine 的
`active_uops` 只是未完成指令的登记表，用于完成回收和排空，不限制容量或退休顺序。
Membar 自身的 retire 时序仍计入 VF 结束周期。UB 一般 RAW/WAR/WAW 不由地址自动推导。

### 4. 逐周期顺序

`execution/engine.py::Engine.run()` 保持以下阶段顺序：

1. 更新 IDU 可见 credit，并交付 IDU→后端传输管线。
2. IFU 填充 IDU；membar 交给控制逻辑。
3. 更新 membar 状态，再执行 IDU dispatch 和后端接收。
4. 处理寄存器/SHQ 释放及 EXU/LSU 完成事件。
5. 更新 ready，先进行一次 LSU 仲裁，再进行 compute 调度，最后使用剩余 LSU 预算。
6. 检查前端、管线、未完成指令和 membar 是否排空。

这些阶段顺序决定 credit 可见性及一周期转发差异，重构保留其时序。
详细文件和函数导览见 [代码结构说明](docs/code_structure_guide.md)。
参数查询直接使用 Profile 和 ParamDB；运行时配置使用 `UarchParameters`，不再通过 TimingQuery 转发。

## 当前默认模型

当前分支是一套 A5 风格双 EXU 参数模型，Python 只使用 `core/simulator.py` 组装的组件后端。

关键默认值来自 `configs/uarch.json`：

| 配置 | 默认值 |
|---|---:|
| IDU window / dispatch width | 6 / 5 |
| SHQ depth | 58 |
| EXQ depth | 26 |
| EXU issue ports | 2 |
| LOAD / STORE ports | 2 / 1 |
| shared UB slots | 2 |
| physical vector registers | 68 |
| consumer release offset | start + 4 |
| SHQ→EXQ policy | `fu_round_robin_exu0_reserve` |
| EXU0 reserve lookahead / min count | 8 / 1 |
| per-EXU inflight cap | 7 |
| Canonical dynamic instruction limit | 20000 |

具体行为以配置和测试为准，不要在文档或调用方中重复硬编码这些值。

## 实验模式

以下 CLI 选项用于理论上限或结构穿刺，不是默认硬件模型：

```bash
python3 main.py --trace INPUT.json \
  --theoretical-limit-vloop-only \
  --out_dir results/theory_vloop
```

```bash
python3 main.py --trace INPUT.json \
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue \
  --out_dir results/theory_direct
```

当前 Python 模型最多使用两个 EXU 端口。三端口能力将由后续独立的 A6 架构实现，
不提供 CLI 开关、模型构造参数或 uarch 模式开关。

## Python API

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_cce("cce_code/GeLU_poly.dsl")
model = CoreVfCostModel(out_dir="results/api_gelu")
result = model.run_vf_info(vf_info)

print(result["vf_end_cycle"])
```

只需要周期时使用 `predict_vf_cycles()`。外部 JSON 先使用 `InputAPI.load_json()`；Core 只接收 Canonical 对象，内部执行准备表示不属于公共输入合同。

旧 Python VFInfo、旧 JSON 和离线转换接口已删除。使用 Canonical JSON、Builder 或原始 CCE 输入。

## Native C++

`native/` 提供与 Python 对应的 C++ 核心，供编译器集成和跨语言一致性验证。正式入口是 `runCanonicalVfInfo()`，正式库目标是 `vfsim::native_core`。

旧 VFInfo、旧 JSON 和历史 normalization 编译到可选的 `vfsim::native_legacy`，只用于离线迁移和历史测试。

构建并运行 Native 测试：

```bash
cmake -S native -B build-native -DVFSIM_BUILD_TESTS=ON
cmake --build build-native -j2
ctest --test-dir build-native --output-on-failure
```

Python 和 C++ 目前是两套实现，Canonical 合同和共享配置一致，但 Python 并不是通过 pybind11 调用 C++。

## 配置文件

核心配置位于 `configs/`：

| 文件 | 内容 |
|---|---|
| `instruction_catalog.json` | 指令语义、参数签名、form 与 specialization |
| `isa.json` | opcode/form 的 latency、class、FU、dispatch EXU 等 |
| `forwarding.json` | producer→consumer 数据可用延迟 |
| `InitiationInterval.json` | previous→current 的 initiation interval |
| `uarch.json` | 队列、端口、preg、延迟、分发策略和 membar 时序 |
| `uarch_override_schema.json` | Canonical uarch override 的字段类型与 Python/C++ 支持范围 |

Catalog 描述“指令语义是否支持”，timing 配置描述“硬件参数是否完整”；两者不能混为一谈。

## 输出与 Perfetto

每次 Python 仿真会在结果目录生成：

- `start_by_cycle.json`：动态指令开始执行记录；
- `done_by_cycle.json`：动态指令完成记录；
- `idu_to_ooo.json`：IDU 分发记录；
- `idu_address_blocked.json`：POST_UPDATE 地址状态阻塞记录；
- `vloop_trace.json`：顶层 loop/block 推进记录；
- `sim_history.json`：逐周期详细状态；
- `model_warnings.json`：参数 fallback 等警告，有警告时生成；
- `trace.json`：Chrome Trace Event JSON。

运行生成的 `trace.json` 可直接上传到 [Perfetto UI](https://ui.perfetto.dev/) 查看 VF、Load Unit、EXU 和 Store Unit 时间线。当前映射使用 `1 cycle = 1 us` 以便显示；它不代表真实微秒时间。

## 目录结构

```text
main.py              Python CLI 入口
api/                 Canonical 输入、CCE parser、adapter 和 validator
api/cce/             CCE 解析、规范化、私有 IR 和值版本化
api/json/            Canonical JSON 格式校验和序列化
api/programmatic/    Builder 程序化构造
api/common/          共享 Canonical 类型、Catalog 和语义校验
api/native/          C++ Canonical 合同、JSON adapter 和共享配置接口
core/                Python simulator 入口及 execution/uarch/common/params/trace
native/              C++ 执行核心、构建目标和 Native 测试
configs/             指令及微架构参数
tests/               Python 单元测试和共享 fixtures
regression_suite/    稳定回归输入、周期 baseline 和精度报告
cce_code/            CCE/DSL 示例与算子实验输入
ascend_runner/       CCE/CAmodel 编译、运行和参数校准工具
optimizer/           split、unroll、DAG 等优化实验
tools/               回归、迁移、日志分析和绘图脚本
docs/                架构、建模规则和开发计划
results/             生成结果，通常不作为源码依赖
```

函数级代码导览见 `docs/code_structure_guide.md`，详细建模规则见 `VF_modeling.md`。文件存在不代表它属于正式预测路径；Native 尚保留独立的 legacy 实现；Python API/Core 的旧输入和 normalization 已删除，优化器本轮不处理。

## 测试与回归

Python 单元测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Smoke 回归：

```bash
python3 tools/run_cost_model_regression.py --tier smoke
```

完整回归：

```bash
python3 tools/run_cost_model_regression.py --tier full
```

只有确认模型语义改变且结果经过审查后才更新 baseline：

```bash
python3 tools/run_cost_model_regression.py --tier full --update-baseline
```

Native 回归先构建 `build-native/vfsim_native_json_runner`，再运行：

```bash
python3 tools/run_native_cost_model_regression.py --tier smoke
python3 tools/run_native_cost_model_regression.py --tier full
```

自动结果默认写入 `results/regression_suite/latest/` 和 `results/native_regression_suite/latest/`。稳定报告位于 `regression_suite/reports/`。

master 默认使用 `regression_suite/cases/baseline_canonical_membar.json`，对应
Canonical 入口、全局 membar 时序模型和平衡 EXU0 预留策略
（`lookahead=8`、`min_count=1`、`cap=7`）。旧 baseline 保留用于历史对比。
历史更新记录见 [master 合并验证](regression_suite/reports/master_canonical_merge_20260914.md)。
本次同步不自动刷新周期基准；新增谓词和搬运指令模型的精度变化需独立核验。

## 模型边界

- 模型预测 VF Core，不自动覆盖 host、GM DMA 和完整 kernel 端到端时间。
- 当前 Core 主要识别寄存器数据依赖；UB 顺序由显式 membar 保护。
- Canonical schema 可以表达结构化 memory/control dependency，但当前 Core 不执行任意显式 dependency edge。
- 缺失 timing 的 fallback 结果需要 warning，不应冒充实测参数。
- 优化器输出必须重新做数值验证；周期降低本身不能证明算子正确。
- `results/`、大型 CAmodel dump、缓存和临时日志通常不应提交，除非已整理为可复现实验材料。
