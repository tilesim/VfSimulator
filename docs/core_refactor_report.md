# Python Core 重构记录

分支：`dev_wch/refactor`，基于用户确认的 `origin/master`（`963686e`）。
工作目录：`E:\VfSimulator_refactor`。本轮保留已经完成的 API 重构，继续整理 Python Core。

## 结果

- Core 根目录仅保留 `simulator.py` 作为公开入口；`main.py` 通过该入口运行。
- 公开输入仅为 `CanonicalVfInfo`。文件读取和 JSON 解码由 InputAPI 完成。
- 执行准备、动态展开、周期推进、调度、loop 和 membar 放入 `execution/`。
- IFU、IDU、Registers、SHQ、EXQ、EXU、LSU 及地址状态放入 `uarch/`，分别拥有组件状态。
- 动态指令、Uop、结果和严格存储类型查询放入 `common/`；参数和日志分别放入 `params/`、`trace/`。
- 删除旧 Core 工厂、旧基类、旧 runner、旧寄存器/循环 normalization 和名称推断路径。
- 删除 ROB 容量及按序退休；完成事件由 EXU、LSU 产生，未完成登记表只用于排空。
- 保留 membar 自身的 retire、循环开放、forwarding、II、寄存器释放和信用反馈时序。
- 参数数据库默认路径适配新目录；内部 UB 对象身份不会覆盖合法的 Canonical value ID。

文件及关键接口见 [代码导览](code_structure_guide.md)。配置文件、Native C++ 和优化器没有改动。

## 验证

| 检查 | 结果 |
| --- | --- |
| Python 单元测试 | 298 项：288 通过、10 项 Native 环境测试跳过 |
| 最终 smoke 回归 | 22 cases 全部通过，与本轮 Core 改动前的周期逐项完全一致 |
| 默认 Canonical JSON CLI | `cycles_executed=35`、`vf_end_cycle=47` |
| GeLU CCE CLI | `cycles_executed=1911`、`vf_end_cycle=1923` |
| 日志对比 | 176 文件逐字节一致；22 文件移除 ROB retire/preg_old 后事件完全一致 |
| 源码语法 | 全部 Python 文件通过 Python 3.10 grammar / Python 3.11 parser 检查 |
| Scope | configs、Native、optimizer 内容与本轮开始时相同 |

新增 Core 边界测试覆盖：拒绝文件/旧字典、按声明而非名称分类、改名不改变周期和
物理调度、UB 内部 ID 不遮蔽 Register、无 ROB 排空、参数数据库默认路径。
现有 loop-carried/unroll、向量/谓词释放、POST_UPDATE、align state、共享 UB 预算和
membar 测试已迁移到新接口。删除的测试对应已删除的旧 normalization 功能。

```bash
python3 -m unittest discover -s tests -q
python3 tools/run_cost_model_regression.py --tier smoke
python3 main.py --cce cce_code/GeLU_poly.dsl --out_dir results/gelu
```

## 接口与日志变化

原 `core.cost_model.CoreVfCostModel` 改为 `core.simulator.CoreVfCostModel`，不保留旧模块
转发。原 `run_payload()` 已删除；Canonical JSON 先通过 `InputAPI.load_json()` 读取。
Core 依赖由组件构造参数显式传入，移除旧 Core 实例的大范围字段转发。

`sim_history.json` 不再包含指令 `retire` 事件和 `preg_old` 字段。
`membar_history.json` 中的屏障 retire 保留。
`model_warnings.json` 移除旧空 `vreg_capacity_warnings`，仍输出指令参数 fallback。

当前执行能力限制保持不变：不自动推导 UB 地址区间依赖；显式 dependency 和不支持的
unroll 形式在准备阶段拒绝。Native 跨语言一致性尚未在本轮环境验证，后续 C++ 重构时处理。
