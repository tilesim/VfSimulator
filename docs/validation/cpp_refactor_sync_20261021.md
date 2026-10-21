# C++ 指令与时序一致性验证

验证日期为 2026-10-21。C++ 实现与共享数据对应
[refactor 提交 4cc2015](https://github.com/tilesim/VfSimulator/commit/4cc20155f36be92e24772e4909bd7b3148c6e389)。

## 代码与配置

C++ 的 91 个源码、头文件及内嵌数据表位于根目录的 `api/`、`core/`、`cli/` 和 `tests/`。
源码比较时移除 `native/` 目录前缀和头文件引用中的对应前缀；CMake 使用仓库根目录作为
公开头文件目录及 `VFSIM_SOURCE_ROOT`。普通构建直接使用已提交的内嵌数据表。

7 份配置逐字节一致。源码映射、配置、JSON schema 和固定 CCE/Canonical/JSON 输入
共 243 个文件核对通过。`VFtest/` 按 `cce/`、`canonical/`、`json/` 分类，
CCE 文件使用 `.cce` 后缀。构建与运行依赖 CMake 和 C++17 编译器。

## 行为检查

| 检查 | 结果 |
| --- | --- |
| 独立 Release 构建 | 通过 |
| CTest 组件和输入测试 | 8 / 8 通过 |
| CCE 完整 Canonical 合同 | 20 组通过，包含 A5 软件展开、转换、谓词与访存 |
| 固定算子与依赖探针 | 37 / 37 周期数与源分支 Python、C++ 基准一致 |
| 发射与完成事件 | 37 / 37 的 `(inst_id, op, cy)` 排序及 SHA256 一致 |
| 参数缺失 warning | 0 |

37 组覆盖 GeLU、SiLU、SwiGLU、Softmax、在线更新、依赖探针和长指令链。
事件比较使用固定输入 SHA256，两个阶段分别比较全部事件数量及哈希。
原始日志保存在独立验证目录；[机器报告](cpp_refactor_sync_20261021.json)保存逐例周期、
输入和事件哈希、参数哈希及测试状态。

## 执行规则与覆盖边界

EXU 按 opcode/form 保留 self-II 历史，并检查相邻发射的转换 II。
默认每 EXU 在途上限为 7，每周期最多发射和完成各一条计算指令。
完成周期冲突通过推迟发射解决，指令 latency 保持配置值。

指令和参数的适用范围见[A5 覆盖进度](../instruction_coverage/a5/progress.md)。
归档输入中记录的 C++ 校验阻塞见[归档指令对报告](../instruction_coverage/a5/membar_pairs.md)。

在仓库根目录运行组件与输入检查：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DVFSIM_BUILD_TESTS=ON
cmake --build build -j4
ctest --test-dir build --output-on-failure
build/vfsim_native_json_runner --cce VFtest/cce/contracts/vector_a5_software.cce --out-dir results/a5_software
```
