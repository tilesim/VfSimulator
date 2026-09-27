# VfSim-tilesim Python 同步记录（2026-09-27）

## 基线与范围

- TileSim `dev_wxk/tilesim_refactor` 已快进到远程 `0f5f17aaa1494c73a047276144031ef5b6a6863c`，工作区无改动。
- VFSim 集成分支先快进到 `45160741b8704a26f01e36e13a8e32c9a65a751c`。
- 本次同步源：`vfinfo-core-api-unification`，`52081ae4ca8a402f96e795c05733a47c2562a656`。
- 选择性同步 Python core、canonical frontend、配置、相关测试/fixture 和 Python 测试辅助工具，不合并整个分支的 native/C++ 改动。
- 上游 api/core/configs 的 Python 和 JSON 与同步源按换行归一化比对，只有三个文件有集成分支差异：
  `api/simulator_costmodel.py`、`core/param_db.py`、`core/simulator_runner.py`，用于保留关闭文件输出和配置缓存优化。
  集成分支专用的 program API、打包工具另行维护。

## 接口适配

- 支持 `PredicateRegister/bool`、真实谓词生产者/消费者、物理寄存器池、循环依赖。
- 支持 Catalog 新指令和 form，包括比较、谓词逻辑、MOVVP、VINTLV/VDINTLV、双结果 VLDS、VLDAS/VLDUS、PSTU 等。
- `VfSimInst.memory_accesses` 使用导出的 `VfSimMemoryAccess`，传递显式地址状态、POST_UPDATE 字节增量和 span；alignment 状态通过 canonical attributes 传递。
- 普通旧式、无显式 predicate storage 的 program 保留 v1 timing-only 输入兼容；真实谓词模式严格要求 producer/mask，未添加默认 PSET 或新 fallback。
- **兼容限制**：旧式比较指令写出 `Register/bool` 需迁移为显式 PredicateRegister，并提供 mask 及其生产者；不能静默视为新谓词模型。
- **MLIR 边界**：`VLDSX2 → vlds(dst0,dst1,ptr,offset,DINTLV_B32)` 由 TileSim MLIR adapter 负责。本分支只接收已经转换的 `VLDS`、两个 dst、`config.catalog_mode=DINTLV_B32`，拒绝原始 VLDSX2。
- 详细示例、安装方式及约束见 [program API](../api/tilesim_program.md)。

## 验证

WSL / Python 3.12：

```bash
# VFSim 根目录；此环境借用系统安装的可选 jsonschema
PYTHONPATH=/usr/lib/python3/dist-packages ../tilesim/.venv/bin/python -m pytest -q tests -k 'not generated_cpp'
python3 tools/sync_python_package.py --check
```

结果：325 passed，179 subtests passed，7 skipped，2 deselected。
7 项为需要 native runner 的测试；2 项为生成 C++ 表与配置一致性检查。本次未同步 native，因此不以旧 native 表/runner 验证新 Python 实现。

构建 0.3.0 wheel 并安装到独立 `/tmp/vfsim-unification-wheel-site` 后：

```bash
cd /mnt/e/tilesim/tilesim
PYTHONPATH=/tmp/vfsim-unification-wheel-site .venv/bin/python -m pytest -q \
  tests/ut/core/backend/vf_costmodel \
  tests/ut/core/backend/tile_op_costmodel/test_a5_reg_add.py \
  tests/ut/core/pipeline/tilesim_eng/test_dsl_to_mir_eng_sim.py
```

结果：54 passed。该集合不意味着所有旧式 bool 比较调用已经迁移。
另从 `/tmp` 运行安装包 smoke，验证谓词程序和双结果加载，确认未导入顶层 `core`/`api`；文档谓词示例为 56 cycles。

产物：`/mnt/e/tilesim/wheels/vfsimulator-0.3.0-py3-none-any.whl`。
SHA256：`eb02e56cd1db422646dbb291e030e69ae3ba7dd83fc7083a8f60838a3bdaa675`。
未替换 TileSim 仓库 wheel 或其默认虚拟环境。此次代码更新尚未 commit/push。

## 配置缓存隔离修复

确认公开浅复制会使嵌套修改污染同实例、已有实例及新实例，文件本身不变。
保留共享解析缓存，在 get_uarch/get_defaults 和旧版 get_inst 返回边界深复制。
新增根实现/命名空间包、v1/v2 ISA 的隔离测试；修复前 12 failed/12 passed，修复后 24 passed。
完整 Python 回归：349 passed、179 subtests passed，7 skipped、2 deselected。
重新构建并更新 TileSim 的 0.3.0 wheel；最新 SHA256：`14566b8df87c9440039d5e708512a29428925b48fa58c0e0dd57c496ea872fe0`。
