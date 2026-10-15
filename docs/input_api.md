# 输入层

`api/input_api.h` 统一接收三种输入：`loadCce()` 读取 CCE/DSL，`loadJson()` 读取 Canonical JSON v1/v2，`fromCanonical()` 接收直接构造的对象。所有入口返回通过语义校验的 `CanonicalVfInfo`，Core 接收校验完成的程序。

## CCE 转换

| 模块 | 职责 |
| --- | --- |
| `source.*` | 去注释、提取 VF scope、kernel 选择、参数声明、括号与源码位置 |
| `parser.*` | 词法作用域、声明、Catalog 调用绑定、循环、别名和 POST_UPDATE |
| `ir.h` | 输入转换的中间值、指令、访存、循环和别名节点 |
| `normalization.*` | 合并声明，统一 dtype、form、opcode 和屏障类型 |
| `affine.*` | 整数常量求值，仿射 offset 与更新量解析 |
| `value_versioning.*` | 静态 definition、alias 快照、循环 entry/back-edge/exit 和 UB 对象 |
| `adapter.*` | 组合转换流程并完成统一语义校验 |

CCE 支持 vector/predicate/标量声明、UB 指针和别名、Catalog 声明的指令调用、inline PSET、带花括号的嵌套 for 循环、显式 unroll 与 membar。for 循环支持非零起点及正步长。未知语句、未声明操作数、不匹配的调用签名和非法地址更新会报错。

`loadCce(path, kernelName, loopParams)` 可选择一个 VF kernel 并提供循环参数。源文件的常量调用参数推断补充参数值。仿射地址支持整数、参数、当前及外层循环变量、加减和常量乘法；变量乘变量会报错。

## 显式构造

`InputAPI::newBuilder(params, uarch, source)` 返回 `VfInfoBuilder`。通过 `registerStorageObject()`、`registerValue()`、`addInstruction()`、`addMembar()` 和 `addLoop()` 添加 Canonical 声明。

`builder.loop(definition)` 返回 C++ 作用域守卫；作用域内继续通过同一 Builder 添加节点。正常退出提交循环 body，异常展开回滚该作用域的节点和值声明。循环作用域按栈顺序关闭，打开作用域时调用 `build()` 会报错。

`build()` 自动选择 schema v1/v2 并校验定义、作用域、依赖、循环与访存语义。源位置、opcode、form 和各操作数均由调用者显式提供。

## JSON 与执行边界

`encodeCanonicalVfInfoJson()` 和 `decodeCanonicalVfInfoJson()` 支持 Canonical 对象与 JSON 值的往返转换，`json::stringify()` 生成 JSON 文本。解码后的直接对象通过 `InputAPI::fromCanonical()` 接收。

Core 支持普通寄存器依赖、循环 carried values 和 Membar。循环 count 与 unroll 的整除条件在展开回退前检查。
