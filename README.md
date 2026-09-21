# VF Simulator

VF Simulator 是面向 Ascend 风格 VF（Vector Function）的周期级性能模型。它根据动态指令依赖、队列容量、物理寄存器、执行端口、forwarding、II、访存端口和 membar 时序，预测一个 VF 从启动到流水线排空所需的周期。

模型不执行算子数值计算。`vf_end_cycle` 用于性能预测；数值正确性仍需通过 CAmodel、设备运行和 golden 数据验证。

当前正式输入合同是 `CanonicalVfInfo v1`。CCE/DSL、Canonical JSON 和程序化 Builder 只是不同前端，最终进入同一套 Core 语义。

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
                       CanonicalVfInfo v1
                   通用校验 + Catalog 语义校验
                               │
                       CoreLoweringPass
             program / values / params / uarch payload
                               │
               ProgramAnalyzer + Flattener + ParamDB
                               │
                    IFU → IDU → OoO Core
                               │
              ┌────────────────┼─────────────────┐
              │                │                 │
       SHQ → EXQ → EXU        LSQ        Membar ControlUnit
          compute path     load/store path       control path
              │                │                 │
              └────────────────┼─────────────────┘
                               │
                       run_simulation()
                         逐周期推进模型
                               │
       vf_end_cycle / JSON 日志 / Perfetto trace / warnings
```

### 1. 输入与 Canonical 前端

公共输入入口在 `api/input_api.py`：

- `InputAPI.load_cce()`：解析 CCE/DSL 中受支持的 VF 语句。
- `InputAPI.load_json()`：严格读取 Canonical JSON。
- `InputAPI.new_builder()`：由 Python 代码直接构造 Canonical 输入。

`CanonicalVfInfo` 定义在 `api/frontend/schema.py`，主要描述：

- instruction、loop 和独立的 membar 控制节点；
- 每一次 register/scalar/UB value definition；
- loop entry、back-edge 和 exit value；
- 稳定的 UB storage object 与访存描述；
- 静态依赖、参数、源码位置及可选微架构覆盖。

CCE 解析器不是完整 C++ 编译器，只解析模型明确支持的 `__VEC_SCOPE__` 内容。host 代码、GM 搬运和完整标量程序不属于 VF Core 预测范围。

#### CanonicalVfInfo 核心定义

Python 定义位于 `api/frontend/schema.py`，C++ 等价定义位于
`api/native/CanonicalVfInfo.h`，跨语言 JSON 合同位于
`api/frontend/canonical_vf_info_v1.schema.json`。

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

### 2. Lowering 与程序分析

`api/frontend/core_lowering.py` 将经过验证的 Canonical 输入转换为 Core payload。`core/program_analysis.py` 分析顶层 block 和 loop 边界，`core/flatten.py` 将结构化程序转换成包含循环边界的线性静态表示。

Lowering 只转换表示，不负责调度。寄存器依赖仍由 definition/value identity 表达，不能用逻辑寄存器名称或静态 PC 代替动态定义。

### 3. IFU：生成动态指令流

`core/ifu.py` 负责：

- 展开循环和受支持的 unroll；
- 维护 loop-carried value 的进入、回边和退出绑定；
- 生成 `static_instruction_id`、`iteration_path`、`stream_seq` 和动态 `inst_id`；
- 标记动态 value instance 的最后使用；
- 跳过零次循环产生的空 top block。

Canonical 动态展开默认受 `canonical_dynamic_instruction_limit=20000` 保护，避免异常输入在仿真开始前无限占用宿主机内存。可在合法 Canonical `uarch` override 中调整，设为 `0` 表示关闭保护。

### 4. IDU：窗口和后端资源入口

`core/idu.py` 保存 IFU 产生的动态指令，并检查：

- IDU 窗口和每周期 dispatch width；
- SHQ、LSQ 和物理寄存器 credit；
- 顶层 loop/block 的开放时刻；
- IDU 到 OoO 的传输延迟；
- POST_UPDATE pointer state 的 dispatch 就绪时刻。

IDU 判断指令是否可以进入 OoO 后端，但不负责最终选择 compute 指令在哪个 EXU 开始执行。

### 5. OoO、ISU 与寄存器

`core/ooo_mainline.py` 是 Python 主执行核心，`core/ooo.py` 提供 Uop 和公共辅助状态，`core/isu.py` 负责 compute 调度。

```text
compute: IDU → rename/RAT → SHQ → EXQ0/EXQ1 → EXU0/EXU1
memory:  IDU → LSQ → load/store pipe
control: IFU → ControlUnit，不进入 IDU/SHQ/EXQ/EXU
```

主线 compute 分发策略为 `fu_round_robin_exu0_reserve`：ALU/SFU 分别 round-robin，并根据 SHQ 前看范围内的 EXU0-only 压力为 EXU0 保留空间。EXQ 内同 FU 保持 FIFO，ALU 与 SFU 可相互乱序。

Rename/RAT 使用 Canonical value definition 建立 producer-consumer 关系。物理寄存器在最后一个 consumer 开始后经过配置延迟，并满足 RAT、pending consumer 等安全条件时归还；不是看到 last-use 就立即释放。

### 6. LSU 与 membar

LOAD/STORE 进入 LSQ，不经过 compute EXQ。当前默认共享两个 UB issue slot：最多可开始两条 LOAD，或一条 LOAD 加一条 STORE；不能在同周期开始两条 LOAD 再加一条 STORE。

`mem_bar(VST_VLD)` 等方向性 barrier 由 `core/control_unit.py` 和 `core/membar_timing.py` 建模：

- membar 是控制节点，不占计算或 LSU 执行单元；
- 只阻塞指定方向的后续 LSU，不直接阻塞无关 compute；
- 有 `membar_timing` 配置时建模 admission、前序 LSU 同步、release、retire 和后续消费者延迟；
- 当前正式主线使用方向性全局 membar，不自动从 UB 地址推导一般 RAW/WAR/WAW 依赖。

UB 地址局部依赖属于专项实验能力，不应与正式全局 membar 基线混用。

### 7. ParamDB 与指令参数

`core/param_db.py` 加载并合并 ISA、forwarding、II 和微架构参数。Uop 在进入 OoO 时绑定不可变 `InstructionProfile`，后续调度复用 profile，而不是每周期重新解析 JSON。

- latency：指令自身执行时序；
- forwarding：producer 结果何时可被特定 consumer 使用；
- initiation interval：同一执行资源上前后两条指令允许开始的间隔；
- dispatch EXU：指令可以进入哪些执行端口；
- op class：决定走 compute、LOAD、STORE 还是控制路径。

缺失的 opcode/form/forwarding/II 可以按兼容规则回退，但会写入 `model_warnings.json`。带 warning 的结果不能直接当成已校准参数。

### 8. 逐周期主循环

`core/simulator_runner.py::run_simulation()` 每个周期大致执行：

1. 更新 IDU 可见的资源 credit，并交付 IDU→OoO 延迟管线。
2. IFU 向 IDU 填充动态指令；membar 直接交给控制单元。
3. 更新 membar 发射、同步、release 和 retire 状态。
4. IDU 向 OoO 分发允许进入后端的指令。
5. OoO 处理完成、退休、资源释放和本周期新发射。
6. 记录日志并检查 IFU、IDU、OoO、LSQ 和控制单元是否全部排空。

该周期顺序是模型语义的一部分，随意交换 control update 与 instruction completion 可能产生一周期偏差。

## 当前默认模型

当前分支是一套 A5 风格双 EXU 参数模型，正式后端只有 `OoOCoreMainline`；`queue_level4` 是保留的模型名称，不表示仓库里还有四套可切换的正式 OoO 实现。

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

```bash
python3 main.py --trace INPUT.json \
  --three-ports \
  --out_dir results/three_ports
```

`--three-ports` 是本分支的实验 override，并不等价于一套完整、独立校准的 A6 SoC 参数。

## Python API

```python
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel

vf_info = InputAPI.load_cce("cce_code/GeLU_poly.dsl")
model = CoreVfCostModel(out_dir="results/api_gelu")
result = model.run_vf_info(vf_info)

print(result["vf_end_cycle"])
```

只需要周期时使用 `predict_vf_cycles()`。外部 JSON 使用 `run_payload()` 或 `InputAPI.load_json()`，不要把 Core lowering 后的内部 payload 当成公共输入合同。

旧 VFInfo 和旧 JSON 不再进入正式预测路径。确需迁移时使用独立 legacy converter；不要继续扩展旧输入结构来承载新语义。

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
api/                 Canonical 输入、CCE adapter、validator 和公共 cost model API
api/frontend/        Canonical schema、builder、Catalog、版本化和 Core lowering
api/native/          C++ Canonical 合同、JSON adapter 和共享配置接口
core/                Python IFU、IDU、OoO、ISU、寄存器、LSU 和控制单元
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

函数级代码导览见 `docs/code_structure_guide.md`，详细建模规则见 `VF_modeling.md`。文件存在不代表它属于正式预测路径；历史 optimizer、legacy adapter 和 normalization 代码仍可能为迁移或专项测试保留。

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

## 模型边界

- 模型预测 VF Core，不自动覆盖 host、GM DMA 和完整 kernel 端到端时间。
- 当前 Core 主要识别寄存器数据依赖；UB 顺序由显式 membar 保护。
- Canonical schema 可以表达结构化 memory/control dependency，但当前 Core 不执行任意显式 dependency edge。
- 缺失 timing 的 fallback 结果需要 warning，不应冒充实测参数。
- 优化器输出必须重新做数值验证；周期降低本身不能证明算子正确。
- `results/`、大型 CAmodel dump、缓存和临时日志通常不应提交，除非已整理为可复现实验材料。
