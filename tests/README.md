# Python 测试

```bash
python3 -m pip install '.[test]'
python3 -m pytest -q tests
python3 tools/sync_python_package.py --check
```

输入、值版本化、执行组件、寄存器、LSU、循环、屏障、II、完成带宽和参数查询测试
使用 `VFtest/` 中的固定输入及独立组件实例。
`core_support.py` 负责准备程序、组装 Engine 和读取执行事件。

`test_program_api.py`、`test_program_predicates.py` 检查 TileSim 对象、显式 storage、
循环、谓词、UB 别名、访存状态和输出开关。
`test_tilesim_integration.py` 检查单次语义校验、标量广播、读写依赖、地址单位、
配置快照复用及更新后的重新加载。
`test_vfsimulator_package.py` 检查生成包与源码一致及命名空间模型的结果。

独立安装 wheel 后还需从仓库外验证包资源与真实 TileSim 调用。
带有外部 Native runner 的 Python 对照测试通过 `VFSIM_NATIVE_RUNNER` 选择匹配的可执行文件。
