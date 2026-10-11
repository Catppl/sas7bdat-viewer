# Listing Generator 无法打开 SQLite database issue

## 实施状态

已完成共享的跨平台 SQLite URI 修复：结果库和 Merge inspection 连接现在显式使用
`uri=True`，Listing/Merge 的 source attach 统一使用只读 `mode=ro` URI，并在 cache
缺失时报告具体 attach 阶段。回归测试已覆盖 source/ADSL、Merge Left/Right、只读保护，
以及包含空格、中文、单引号和 `#` 的路径。

本地测试不能完全代替 Windows 打包程序验收；发布前仍需在 Windows exe 中分别验证
Merge，以及启用/不启用 ADSL 的 Listing。

记录日期：2026-08-31  
状态：已记录，暂未修改程序

现场截图：

- `/tmp/codex-remote-attachments/01a02181-f1ab-7ac3-b738-df356376b7ae/047F4057-068C-44D2-A116-0EF17F122169/1-Photo-1.jpg`

## 1. 用户可见现象

在 `Tools > Listing Generator` 完成配置后点击 `Run Listing`，任务失败并提示：

```text
unable to open database file
```

期望行为：

- Listing 能读取已经完成 cache 的 source dataset；
- 如果启用 ADSL merge，也能读取已完成 cache 的 ADSL dataset；
- 程序能够在自己的 session temporary directory 中创建 Listing 结果 SQLite；
- 如果某个数据库路径失效或不可写，错误信息应明确指出失败阶段和目标，而不是只显示通用
  SQLite 错误。

## 2. 当前调用链

涉及文件：

- `clinical_data_viewer/controllers/analysis/listing.py`
- `clinical_data_viewer/listing/engine.py`
- `clinical_data_viewer/listing/result_store.py`
- `clinical_data_viewer/temp_manager.py`
- `clinical_data_viewer/settings.py`

点击 Run 后的主要数据库操作为：

1. `TempManager.create_dataset_directory()` 创建结果目录；
2. `ListingResultWriter(directory / "dataset.sqlite", ...)` 创建结果库；
3. `ListingEngine._base_table()` attach source cache；
4. 如果启用 ADSL merge，attach ADSL cache；
5. 在结果库内创建 `base`、写入 Listing records 和 `cache_info`。

当前 source/ADSL URI 由以下逻辑生成：

```python
handle.database_path.resolve().as_uri() + "?mode=ro"
```

然后作为参数传给：

```sql
ATTACH DATABASE ? AS source_db
ATTACH DATABASE ? AS adsl_db
```

而结果主连接由普通的 `sqlite3.connect(database_path)` 创建，并未显式设置
`uri=True`。

## 3. 现场截图证据与当前结论

用户提供的 Windows exe 截图显示：

```text
Listing Generator Failed
unable to open database: file:///C:/Users/acai/AppData/Local/
ClinicalDataViewer/temp/.../dataset.sqlite?mode=ro

File "clinical_data_viewer\listing\engine.py", line 274, in run
File "clinical_data_viewer\listing\engine.py", line 186, in _base_table
sqlite3.OperationalError: unable to open database: file:///C:/...
```

对应当前代码第 186 行：

```python
connection.execute("ATTACH DATABASE ? AS source_db", (_uri(source),))
```

因此现在可以排除：

- 不是 `ListingResultWriter` 创建结果 SQLite 时失败；
- 不是写入 Listing records 时失败；
- 此次截图也不是 ADSL attach 阶段失败。

本次错误已明确定位为：**Windows exe 在 `_base_table()` 中 attach source cache 的
`file:///C:/.../dataset.sqlite?mode=ro` URI 时失败。**

最优先的根因是主结果连接使用普通 `sqlite3.connect(database_path)` 创建，随后把
URI filename 交给 `ATTACH DATABASE`；该组合在当前 Windows 打包环境下没有把 URI
按预期作为只读 SQLite 文件打开。错误信息原样保留 `file:///...?...` 也支持这一判断。

仍需在修复时验证 source cache 文件确实存在，避免只修 URI 后掩盖独立的 cache 生命周期
问题；但排查范围已经从三个数据库阶段缩小到 source attach。

### 本机回归现状

本机现有回归结果：

```text
python -m unittest tests.test_listing tests.test_listing_builder_state
Ran 18 tests
OK (skipped=3)
```

因此该问题不能在本机基础 fixture 中复现，属于现有测试没有覆盖到的 Windows
`ATTACH DATABASE + file URI + mode=ro` 兼容路径。

修复前的剩余验证顺序：

1. source `handle.database_path` 指向的 cache 是否仍存在；
2. Windows 下 `file:///C:/.../dataset.sqlite?mode=ro` 通过 `ATTACH DATABASE`
   使用时是否正确解释为 URI；
3. 路径中含空格、中文、单引号、`#`、`?`、非 ASCII 字符或超长路径时是否失败；
4. source reload、关闭 tab、temporary cleanup 或 controller retain/release 是否提前删除 cache；
5. 启用 ADSL merge 时，同一修复是否也适用于 `adsl_db` attach。

截图已经确认当前故障点不是结果目录创建。实现时仍应保留分阶段错误信息，以便未来能够区分
source attach、ADSL attach 和结果写入问题。

## 4. 后续施工步骤

### Step 1：保留原始错误并标记失败阶段

在不改变业务语义的前提下，把数据库操作拆分为可诊断阶段：

- `create_result_database`
- `attach_source_database`
- `attach_adsl_database`
- `build_base_table`
- `write_result`

捕获 `sqlite3.Error` 时保留原始异常，并在上层补充：

- 失败阶段；
- source/ADSL/result 路径是否存在；
- parent directory 是否存在；
- result parent 是否可写；
- 不在普通 UI 中泄露无关用户目录内容。

不要用后续 cleanup 异常覆盖最初的 SQLite 错误。

### Step 2：运行前进行只读路径验证

在 worker 开始写结果前验证：

- source cache path 存在且为文件；
- source cache parent 仍属于有效 session；
- merge 模式下 ADSL cache 同样有效；
- result directory 创建后确实存在；
- result SQLite 尚不存在或可以按既定生命周期创建。

验证失败时给出明确、可操作的错误，例如重新打开/reload source，而不是继续到 SQLite 通用
报错。

### Step 3：统一安全的只读 SQLite 打开方式

检查项目其他 analysis engines 的只读连接实现，并优先修复 source/ADSL attach：

- 明确验证 `ATTACH DATABASE` 对 URI filename 的跨平台支持，并确保连接以正确 URI
  模式工作；或
- 使用经过验证的 filesystem path attach，并依靠 source cache 生命周期保证只读；或
- 将只读 source 连接与结果连接的策略封装为经过 Windows 测试的 helper。

重点验证 Windows exe，不根据 macOS 单机结果直接认定兼容。

不要仅删除 `?mode=ro` 后宣称完成；必须同时验证：

- source 数据不会被 Listing 修改；
- source 与 ADSL attach 均可用；
- 带特殊字符的 Windows 路径可用；
- 失败时仍保留最初的 SQLite 错误和具体阶段。

### Step 4：保证 cleanup 不遮蔽根因

`ListingEngine.run()` 失败后仍需清理半成品结果目录，但必须：

- 保存并重新抛出第一次异常；
- cleanup 失败只作为附加诊断；
- 不删除 source/ADSL cache；
- 不解除 Builder source 或清空用户配置。

## 5. 修改边界

本 issue 修复时不要修改：

- Listing expression 或计算语义；
- Listing sorting；
- ADSL left-merge 业务规则；
- Listing JSON contract；
- SAS Code Generator/Jinja2 template；
- Builder Columns 和 Filter UI；
- 其他 analysis engine 的数据库策略，除非抽取的是已经验证过的共享 helper。

## 6. 必需测试

### Engine/database tests

1. 普通 source cache 可以成功生成 Listing；
2. source database 不存在时，错误明确标记 `attach_source_database`；
3. ADSL database 不存在时，错误明确标记 `attach_adsl_database`；
4. result parent 不可写时，错误明确标记 `create_result_database`；
5. 路径包含空格、中文、单引号、`#` 和非 ASCII 字符时可以运行；
6. Windows 风格 `file:///C:/.../dataset.sqlite?mode=ro` URI 的专门回归测试；
7. 不启用 ADSL 时不尝试 attach ADSL；
8. 失败后半成品 result directory 被清理；
9. cleanup 异常不覆盖第一次 SQLite 异常；
10. source/ADSL cache 在失败后仍存在。

### Controller/lifecycle tests

1. source reload/close 被 active Listing task 正确阻止；
2. Run 期间 source 和 ADSL temporary directories 被 retain；
3. task 成功或失败后 input-tab busy state 正确释放；
4. Run 失败不清空 Builder 配置和固定 source；
5. 修复后用户可以直接重试 Run。

### 回归验收

- `tests/test_listing.py`
- `tests/test_listing_builder_state.py`
- Listing controller tests
- TempManager tests
- UI smoke tests
- 项目全量测试
- Ruff
- `git diff --check`
- Windows 打包 exe 上使用真实 `.sas7bdat` 和 `.xpt` 各运行一次；
- Windows 上分别验证启用和不启用 ADSL merge。

## 7. 完成标准

- 正常 source 点击 `Run Listing` 可以生成结果；
- Windows exe 不再因 source/ADSL attach URI 或 temp path 失败；
- 路径失效或权限不足时提示准确失败阶段；
- 失败不会清空 Listing 配置或删除输入 cache；
- 不改变 Listing 数据内容、排序、JSON 和 SAS generator 行为。
