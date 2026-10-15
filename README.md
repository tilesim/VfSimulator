# VfSimulator

VfSimulator 是面向 Ascend 风格 VF（Vector Function）的周期级性能模型。
模型根据指令依赖、队列容量、物理寄存器、执行端口、forwarding、II、UB 预算和
Membar 时序，预测向量函数从启动到执行排空的周期。

Python 输入层支持 CCE/DSL 文件、Canonical JSON 文件和 `CanonicalVfInfo` 对象。
API 统一接收并校验数据，Core 将程序展开为动态指令，再按周期协调微架构组件。
模型输出周期、执行事件和 Perfetto 时间线。算子数值正确性通过独立 golden、CAModel
或设备运行验证。

## 运行环境

Python 实现使用 Python 3.10 及以上版本。Canonical JSON 的格式校验需要 `jsonschema`：

```bash
python3 -m pip install jsonschema
```

在仓库根目录执行示例命令。Python 稀疏检出包含 API、Core、配置、测试及回归输入；
完整检出还提供 C++、优化器、校准工具和硬件探针的配套源码。

## 快速开始

运行包含 `__VEC_SCOPE__` 的 CCE/DSL：

```bash
python3 main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu
```

运行 Canonical JSON：

```bash
python3 main.py --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json --out_dir results/fixture
```

默认 JSON 示例的执行 cycle 为 35，包含 drain 的 VF 结束 cycle 为 47。
`--trace` 指定输入 JSON 文件，`--cce` 指定源码文件，两种输入互斥。
省略输入参数时使用默认 Canonical fixture。
一个 CCE 文件含多个 VF kernel 时通过 `--cce-kernel NAME` 选择函数。
`--out_dir` 指定日志目录，相对路径以仓库根目录为基点。

## Python 调用

```python
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel

vf_info = InputAPI.load_json("tests/fixtures/canonical_vf_info/v1_valid_loop.json")
model = CoreVfCostModel(out_dir=None)
result = model.run_vf_info(vf_info)
print(result["vf_end_cycle"])
```

`predict_vf_cycles(vf_info)` 返回整数周期；`run_vf_info(vf_info)` 返回结果字典。
`out_dir=None` 关闭文件输出。直接构造的对象通过 `InputAPI.from_canonical()` 接收；
`InputAPI.new_builder().build()` 返回校验成功的 Canonical 对象。
程序化构造和诊断示例见 [Python 接口](api.md)。

## 执行结构

```text
CCE / Canonical JSON / CanonicalVfInfo
                 │
              InputAPI
        解析、构造和语义校验
                 │
         CoreVfCostModel
    参数组装、程序准备和动态展开
                 │
               Engine
       全局周期、传输和完成事件
                 │
              IFU → IDU
                 │
        重命名、资源预留和入队
           ┌─────┴──────┐
     SHQ → OoO → EXQ   LSQ / LSU
              │         │
             EXU        UB 预算
           └─────┬──────┘
           周期结果和执行日志
```

Registers 维护数据依赖、物理资源生命周期和对齐状态。
LoopControl 控制循环与 block 的开放时间；Membar 控制器管理访存同步。
各组件维护自身状态和局部约束，Engine 安排周期阶段及组件调用。

## 目录与配置

| 目录或文件 | 职责 |
| --- | --- |
| `main.py` | Python 命令行入口 |
| `api/` | 输入接收、Canonical 数据、Catalog、校验及诊断 |
| `core/execution/` | 程序准备、动态展开、周期、循环和屏障控制 |
| `core/uarch/` | IFU、IDU、Registers、SHQ、OoO、EXQ、EXU 和 LSU |
| `core/params/` | 参数加载、结构化记录、缓存查询和缺失参数策略 |
| `core/trace/` | 结果、事件日志和 Perfetto 输出 |
| `configs/` | 指令语义、微架构资源、ISA、forwarding 和 II |
| `tests/` | 自动化测试、组件辅助工具和固定 fixture |
| `regression_suite/` | 算子输入、周期基线、误差指标和报告 |
| `cce_code/`、`ascend_runner/` | CCE 示例、探针及编译运行和校准工具 |
| `optimizer/`、`tools/` | 计划搜索、分区、回归、生成和分析工具 |
| `native/` | C++ 子项目：api、core、cli、tests 和 CMake 构建 |
| `docs/` | 模块、参数、模型和验证说明 |

微架构基础配置位于 `configs/uarch.json`，单次覆盖由 Canonical 的 `uarch` 提供。
默认 EXU 端口为 2，LOAD/STORE 端口为 2/1，共享 UB 预算为 2 槽、512 B/cycle，
vector/predicate 物理池为 68/32，SHQ 深度为 58，每端口 EXQ 深度为 26。
配置加载、字段类型和理论模式见 [配置参数](docs/configuration.md)。
优化工具的模块职责和仿真评估前提见 [优化工具](docs/optimization_tools.md)。

## 输出与验证

结果字典包含 `cycles_executed`、`vf_end_cycle`、`linear_inst_count`、`results_dir` 和
`trace_path`。`vf_end_cycle` 取最后指令完成或屏障退休的最大周期，再加 VF drain。

启用输出时生成开始/完成、IDU 分发与阻塞、VLOOP、详细事件和 Perfetto 文件。
`model_warnings.json` 在存在缺失参数诊断时生成。
`trace.json` 可用 Perfetto 查看；时间戳采用 1 cycle → 1 μs 的显示映射，预测结果的单位为 cycles。
结果字段和日志格式见 [验证与精度](docs/validation.md)。

```bash
python3 -m unittest discover -s tests -q
python3 tools/run_cost_model_regression.py --tier smoke
python3 tools/run_cost_model_regression.py --tier full
```

测试分组见 [tests/README.md](tests/README.md)。回归脚本使用
`regression_suite/cases/baseline_canonical_membar.json` 作为默认周期参考。

## C++ 实现

完整检出的 `native/` 提供 C++17 执行核心，库目标为 `vfsim::native_core`，
输入接口为 `InputAPI::loadJson()` / `fromCanonical()`，仿真接口为
`CoreVfCostModel::runVfInfo()`。核心按 execution/、uarch/、params/、trace/ 组织，
组件分别维护硬件状态与本地时序。目录与调用说明见 [C++ 仿真器](native/README.md)。

```bash
cmake -S native -B build-native -DVFSIM_BUILD_TESTS=ON
cmake --build build-native -j2
ctest --test-dir build-native --output-on-failure
```

将 `VFSIM_NATIVE_RUNNER` 设置为构建出的 `vfsim_native_json_runner` 路径，
可运行 Python 测试中的双实现周期和事件对比。Python 和 C++ 分别维护执行实现。

## 文档

- [Python 接口](api.md)：输入、Builder、预测结果和诊断的调用示例。
- [建模说明](VF_modeling.md)：依赖、资源、控制及模型的适用范围。
- [文档目录](docs/README.md)：各模块及具体建模规则的详细说明。
