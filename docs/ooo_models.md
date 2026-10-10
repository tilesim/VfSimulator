# Python 微架构模型

当前公开入口是 `core/simulator.py::CoreVfCostModel`，仅接收 `CanonicalVfInfo`。
旧 OoO 模型 selector、工厂、VFInfo 入口和 ROB 已删除。

## 当前模型

默认采用双 EXU 的组件模型，主要参数为：

- consumer release：最后消费者 start + 4，并满足生命周期安全条件
- SHQ depth：58
- EXQ depth：26
- EXU issue ports：2
- LOAD / STORE ports：2 / 1
- per-EXU inflight cap：7

```bash
python3 main.py --trace tests/fixtures/canonical_vf_info/v1_valid_loop.json --out_dir results/default
python3 main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu
```

## 实现

- `core/simulator.py`：唯一模型入口及参数组装。
- `core/execution/engine.py`：逐周期推进、传输和完成事件。
- `core/execution/scheduler.py`：SHQ→EXQ→EXU 调度策略。
- `core/execution/loop_control.py`、`membar.py`：循环和屏障时序。
- `core/uarch/`：队列、寄存器、端口及执行状态。
- `core/params/uarch.py`：当前模型参数规范化及理论上界覆盖。

详细说明见 [代码结构导览](code_structure_guide.md)。

## 理论上界参考

```bash
python3 main.py --trace INPUT.json \
  --theoretical-limit-vloop-only \
  --out_dir results/theory_vloop

python3 main.py --trace INPUT.json \
  --theoretical-limit-vloop-only-legacy-forwarding-direct-issue \
  --out_dir results/theory_direct
```

这些模式用于估计优化上界，保留各自指定的控制和依赖时序。

## 架构范围

当前 Python 模型最多使用两个 EXU 端口，不提供三端口 CLI、构造参数或模式开关。
三端口能力将由后续独立的 A6 架构实现。旧实验开关会明确报错；将 `issue_ports`
设为大于 2 的值，也不能在当前模型中启用 A6。

历史报告中的模型名称和实验开关不代表当前可用接口。
