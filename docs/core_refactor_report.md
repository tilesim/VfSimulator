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

## 后续调整：移除三端口实验开关

删除 Python 的 `--three-ports`、`CoreVfCostModel.three_ports` 和三端口端口选择分支。
`three_ports_mode` 从 override 支持列表移入废弃字段列表，并同步生成的共享配置合同。
当前 Core 拒绝 `issue_ports > 2`；通用队列/执行组件仍按端口数量组织状态，供后续 A6 复用。
三端口能力将由独立的 A6 架构实现。其他配置整理和 C++ 执行核心重构仍留待后续。

此调整验证：303 项 Python 测试（293 通过、10 项 Native 测试跳过）；默认程序仍为
47 cycles，GeLU CCE 仍为 1923 cycles。新增测试覆盖旧 CLI/构造开关拒绝、JSON/Builder
废弃字段拒绝、直接 Canonical/配置文件不能启用三端口，以及默认双端口调度。

## 后续调整：删除参数缓存统计

移除模型的公开统计开关、结果中的参数缓存统计项，以及 ParamDB 的命中/未命中计数
和统计查询方法。Profile、forwarding、II 和指令参数缓存仍保留。
缓存测试改为直接验证 Profile 对象复用，并验证缓存命中时不会重新查询或计算参数。

此调整验证：303 项 Python 测试（293 通过、10 项 Native 测试跳过）；默认程序仍为
47 cycles，返回结果不再包含缓存统计。此前三端口移除改动一并保留。

## 后续调整：通用校验归入 API

新增 `InputAPI.from_canonical()`，接收直接实例化的 Canonical 对象。CCE、JSON、Builder
和直接对象统一使用 `api/common/validator.py::require_valid_canonical_vf_info()` 输出：
检查对象类型、执行一次通用语义校验、返回原 Canonical 对象，不引入包装类型或校验标记。
Core 删除通用 validator 的导入和调用，保留类型检查、当前执行能力检查和运行期资源约束。

Builder 的 `build()` 原本已有校验；此前缺失的是直接实例化对象的接收入口。修改 API
输出对象中的 Mapping 后，调用方需重新通过 API 接收。Core 的正式调用前提是输入已经
经过 API 校验，不再在每次预测时重新完整校验。

此调整验证：306 项 Python 测试（296 通过、10 项 Native 测试跳过）。测试通过记录实际
validator 函数执行次数，验证 CCE/JSON/直接对象/Builder 从输入接收到 Core 预测都只调用
一次完整语义校验，并验证合法但当前不可执行的 unroll 仍由 Core 能力检查拒绝。

GeLU CCE 复核仍为 `cycles_executed=1911`、`vf_end_cycle=1923`。
