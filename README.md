# VfSimulator Python · TileSim 接口

VfSimulator 对 VF 指令依赖、物理寄存器、队列、执行端口、LSU、循环和屏障进行逐周期
仿真，返回包含 VF drain 的结束周期及执行事件。Python 核心、Canonical 输入和指令参数
与 `wch/VfSimulator-refactor` 共享，TileSim 通过独立的 `vfsimulator` 命名空间调用模型。

## 安装与调用

Python 版本要求为 3.10 及以上。在本仓库运行：

```bash
python3 -m pip install .
```

TileSim 使用下面的公开接口：

```python
from vfsimulator import (
    StorageKind, VfSimValue, VfSimInst, VfSimProgram, predict_from_program,
)

program = VfSimProgram(
    values={
        "buffer": VfSimValue("buffer", StorageKind.UB, "fp32", (128,), "allocation"),
        "x": VfSimValue("x", StorageKind.REGISTER, "fp32"),
        "y": VfSimValue("y", StorageKind.REGISTER, "fp32"),
        "mask": VfSimValue("mask", StorageKind.PREDICATE_REGISTER, "bool"),
    },
    body=[
        VfSimInst("PSET_B32", [], ["mask"]),
        VfSimInst("VLDS", ["buffer"], ["x"]),
        VfSimInst("VADD", ["x", "x", "mask"], ["y"]),
        VfSimInst("VSTS", ["y", "mask"], ["buffer"]),
    ],
)
result = predict_from_program(program, dump_results=False)
print(result["cycles"])
```

结果保留 `cycles`、`model`、`raw` 和 `trace_path`。`dump_results=False` 关闭仿真文件输出；
`dump_trace_path` 可独立保存 Canonical JSON。输入字段、访存单位与配置缓存见
[TileSim 对接说明](docs/tilesim_integration.md)。

## 输入与执行

```text
TileSim → VfSimProgram → api/tilesim/ → CanonicalVfInfo
CCE / Canonical JSON / CanonicalVfInfo → InputAPI
                                         │
                                  CoreVfCostModel
                                         │
                                       Engine
                                         │
                            IFU → IDU → SHQ → OoO → EXQ → EXU
                                   └──────── LSU / UB
                                         │
                                 周期、事件和 Perfetto
```

输入在 API 出口完成校验。各硬件组件持有自己的队列和状态，Engine 安排周期阶段。
EXU 按 opcode/form 保留 self-II 历史，检查相邻指令 II，默认每端口最多 7 条在途指令，
每周期最多发射和完成各一条计算指令。

源码工作区的命令行入口：

```bash
python3 main.py --cce VFtest/cce/GeLU_poly.cce --out_dir results/gelu
python3 main.py --trace VFtest/canonical/fixtures/v1_valid_loop.json --out_dir results/fixture
```

安装包也提供 `InputAPI`、`CoreVfCostModel` 和 `CanonicalVfInfo`，用于文件输入与直接构造。
JSON 格式校验使用可选依赖：`python3 -m pip install '.[json]'`。

## 目录

| 目录 | 职责 |
| --- | --- |
| `api/cce/`、`api/json/`、`api/programmatic/` | CCE、JSON、Builder 输入 |
| `api/common/` | Canonical、Catalog、校验与诊断 |
| `api/tilesim/` | TileSim 对象定义、Canonical 转换与预测接口 |
| `core/execution/` | 指令准备、展开、Engine、循环与屏障控制 |
| `core/uarch/` | IFU、IDU、SHQ、OoO、EXQ、EXU、LSU 和寄存器组件 |
| `core/params/`、`core/trace/` | 参数快照与查询、执行结果和事件 |
| `configs/` | Catalog、软件展开、资源、latency、II 和 forwarding |
| `vfsimulator/` | 从 `api/`、`core/`、`configs/` 生成的安装包，导入均位于同名命名空间下 |
| `VFtest/` | 按 CCE、Canonical、JSON 分类的固定输入 |
| `tests/` | Python 模型、对接和安装包测试 |
| `regression_suite/` | 固定算子、周期参考与回归清单 |
| `tools/` | 包同步、指令覆盖检查和周期回归 |

修改源代码或参数后，通过同步工具更新安装包：

```bash
python3 tools/sync_python_package.py
python3 tools/sync_python_package.py --check
python3 -m pytest -q tests
python3 -m pip wheel --no-deps -w dist .
```

TileSim 安装 wheel 后，通过 `vfsimulator` 访问模型及随包提供的配置。
[文档目录](docs/README.md)列出模型、参数和验证说明。
