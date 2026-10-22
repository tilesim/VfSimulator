# TileSim 对接

TileSim 将 VF 实体转换为本包导出的 `VfSimProgram`，调用 `predict_from_program()`，
并用 `cycles / clock_freq` 得到时间。VfSimulator 负责向量函数内部的指令、访存和屏障；
外部 tile 调度、MTE、Cube 与主频换算由 TileSim 完成。

## 公开对象

`vfsimulator` 导出 `VfSimValue`、`VfSimInst`、`VfSimLoop`、`VfSimMembar`、
`VfSimProgram`、`VfSimMemoryAccess`、`StorageKind`、`MembarType`、
`program_to_canonical()` 和 `predict_from_program()`。

`VfSimValue` 显式声明名称、storage、dtype 和 shape。UB 视图通过
`storage_object_id` 共享分配身份，`storage_shape` 表达连续分配的行主序步幅。
每个 src/dst 名称都必须存在于 `values`；名称本身不决定存储类别。

`VfSimInst.src/dst` 分别按 Catalog 的输入、输出顺序提供，多结果目的名称互异。
读写操作数在 src 中引用旧定义，在 dst 中生成新定义。
`config` 保存指令属性，如 `catalog_mode` 和对齐状态；`program.uarch` 保存本次资源覆盖。
标量操作数需要显式提供。标量源 `VDUP` 规范化为物理指令 `VDUPS`。

`VfSimLoop` 保存 count、unroll 和循环变量名，可嵌套并引用 `program.params`。
`VfSimMembar` 是按序的屏障节点；`config.has_barrier` 是调用方注解。

## Canonical 边界

`api/tilesim/adapter.py` 绑定 Catalog 的 form、输入、输出、访存模式和类型，复用
`ValueVersioningPass` 构建 definition、循环回边与别名关系，通过 `InputAPI.from_canonical()`
完成一次统一校验。Core 接收 Canonical 程序并使用当前 execution/uarch 组件运行。

任何 `PredicateRegister/bool` 声明会启用完整谓词模型。谓词使用需要可见的生产者，
普通算术和 STORE 需要提供其谓词输入。无 PredicateRegister 的输入使用 v1
时序接口，可省略普通指令末尾谓词；末尾 `Register/bool` 作为时序掩码接收。
比较结果和谓词数据需要显式的 PredicateRegister 及生产者。

`VLDSX2` 由调用方降低为带两个 dst 和 `catalog_mode=DINTLV_B32` 的 `VLDS`。
接口校验该模式的输出、span 和地址单位。`VINTLV/VDINTLV` 支持两个源与两个结果。

## 访存单位

`VfSimValue.offsets` 是每维仿射元素索引，使用 `storage_shape` 或 shape 计算行主序偏移。
`VfSimMemoryAccess` 可覆盖某个 UB 操作数的 offset、span、地址状态与 POST_UPDATE。
未显式指定 `address_unit_bytes` 的 offset/span 使用元素单位；Catalog 要求固定地址单位时，
对接层执行单位转换。显式单位按调用方声明保留并校验。

`post_update_delta_bytes` 始终使用字节。POST_UPDATE 需要完整的地址状态 ID、模式和增量。
多余、重复、非 UB 或与 src/dst 方向冲突的访存记录会报错。
对齐状态使用 `align_state_id` 和 `align_state_operation` 属性。

## 预测、缓存和输出

```python
result = predict_from_program(
    program,
    config_root=None,
    out_dir="results/vf",
    model="mainline",
    dump_trace_path=None,
    dump_results=False,
)
```

结果字段为 `cycles`、`model`、`raw` 和 `trace_path`，`cycles` 对应 `raw.vf_end_cycle`。
`model` 接收 `mainline`。`dump_trace_path` 保存 Canonical JSON；仿真输出开启时，
`out_dir/trace.json` 保存 Perfetto 时间线。两类输出独立控制。

默认配置来自包内 `configs/`。`config_root` 可指定包含 configs 的目录，
ISA、uarch、forwarding 和 II 路径也支持参数加载器的环境变量。
对接层最多缓存 8 个模型，键包含配置根目录、输出位置、fallback dtype 和所选参数文件版本。
路径或文件版本改变时使用新的参数快照。每次预测创建独立的查询缓存、警告和硬件状态。

## 包与源码同步

`api/`、`core/`、`configs/` 是模型源文件。
`tools/sync_python_package.py` 生成 `vfsimulator/` 下的对应文件，
将绝对导入统一限定到 `vfsimulator.api` 和 `vfsimulator.core`。
`--check` 校验文件内容与数量；安装包包含 7 份配置和 Canonical JSON schema。

安装验证需要从仓库外导入 wheel，检查配置资源、对接调用以及 TileSim 的 VF 测试。
模型与外部应用在各自命名空间下导入，仿真器代码通过包内路径读取资源。

## TileSim 的 Catalog 和断言适配

当前 Catalog 扩展了指令名称，访存模式通过显式地址单位表达跨度。
[验证报告](validation/tilesim_python_sync_20261021.md)记录 TileSim 本机版本的两类适配点，
并提供[可审阅的下游补丁](tilesim_compatibility/catalog_and_memory_units.patch)。
补丁在临时副本验证，原仓库的应用可独立进行。
