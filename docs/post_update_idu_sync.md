# POST_UPDATE IDU 修订同步记录

日期：2026-09-17。当前分支：`cpp-native-vfsim`。来源：master `a4d355f`。

## 改动范围

将首版 LSU start 地址约束替换为 IDU dispatch scoreboard。成功分发的
POST_UPDATE 在 dispatch + idu_post_update_ready_latency 后使地址就绪；
默认值为 1，队头受阻时后续指令不能绕过。OoO/LSU 不再绑定或等待地址更新事件，
已进入后端的同指针访问可在资源允许时双发。保留本分支的调度、SoC 和 UB 策略。

旧参数 lsu_post_update_ready_latency 明确拒绝，不能静默接受。
新增 idu_address_blocked.json；成功分发日志记录 dispatch-based 依赖快照。

## 验证

- Python unittest：本分支不适用（纯 C++）。
- Native CTest：7/7。
- A5 AABBCC U1/U2/U4/U8 POST_UPDATE：189/186/276/290 cycles；
  显式 offset：189/194/198/189 cycles。Python/Native 逐项一致。
- 同指针、独立指针、显式 offset 三种积压 probe 均为 159 cycles。
  同指针 IDU dispatch 为 26..41，13 条地址阻塞记录；另外两种无地址阻塞。
  三种模式均允许积压后的 16 条 load 在 8 个周期中双发。

- 仅移植 C++、配置和说明，不加入 Python 执行代码。
- 使用 vfinfo-core-api-unification 的测试驱动对本分支新构建的 Native binary
  额外运行 19 项 POST_UPDATE 测试、八组 AABBCC 和三组积压对照，均通过。

## 复现

`docs/post_update_address_dependency_plan.md` 第 11 节保留 master 修订时的历史
结果与命令，不代表所有分支都有相同的 Membar 或调度能力；本记录优先。
构建本分支的 Native 后运行 CTest。含 Python 的分支可用
`VFSIM_NATIVE_RUNNER=<本分支对应 runner> python3 -m unittest discover -s tests`。
专项脚本为 `tools/run_post_update_validation.py` 和
`tools/run_post_update_idu_backlog_validation.py`，传入 `--out-dir`；
具备对应 Native 能力时再传 `--native-runner`。纯 C++ 分支使用上述外部测试驱动。

本次只同步本地分支，未 push；旧 mem-bar-local-dep-mvp worktree 历史快照未修改。
