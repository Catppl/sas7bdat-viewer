# Listing Generator 模块审查与实施计划

审查日期：2026-08-30  
审查基线：`3047255` (`update builder`)

## 1. 文档目的与施工边界

本文件记录当前 Listing Generator 的代码审查结果，以及后续可直接执行的分阶段施工方案。

本次审查没有修改 Listing 的 Engine、Builder、JSON 或 SAS Generator。当前工作区中已有的 `main_window.py`、`test_ui_smoke.py` 修改属于其他功能，不在本文施工范围内，后续实施时必须保留并避开。

审查范围：

- `clinical_data_viewer/listing/models.py`
- `clinical_data_viewer/listing/expressions.py`
- `clinical_data_viewer/listing/engine.py`
- `clinical_data_viewer/listing/configuration.py`
- `clinical_data_viewer/listing/result_store.py`
- `clinical_data_viewer/ui/listing_builder.py`
- `clinical_data_viewer/controllers/analysis/listing.py`
- `clinical_data_viewer/codegen/sas/listing_generator.py`
- `clinical_data_viewer/codegen/sas/templates/listing.sas.j2`
- Listing 相关 Engine、Builder、Controller、JSON 和 SAS codegen tests

实施原则：

1. 保留 Listing JSON v1 顶层结构、Analysis Controller 边界和现有结果生命周期。
2. 先修统计/表达式正确性与 Python-SAS parity，再做性能和 UI 增强。
3. 每一阶段独立提交、独立回归；不要一次同时改表达式语义、SQLite pipeline 和 Builder UI。
4. 不引入任意 Python/SAS 代码执行，不允许绕过 AST contract。
5. 不顺手实现 RTF、PDF、Title、Footnote、CASE WHEN、跨 derived-column 引用等 v1 以外能力。

### 1.1 已实施更新记录（2026-08-30）

以下项目已在审查后实施，后续施工不得回退：

- Generated SAS 的 ADSL merge 不再检查 missing BY，也不再执行 duplicate-BY SQL/ABORT。该决定基于业务约束：ADSL 不存在 missing USUBJID，且每个 USUBJID 唯一。Python Listing Engine 原有安全校验暂时保留，不属于本次 SAS code修改。
- PROC REPORT 的 `column` statement 改为单行并以分号结束。
- 每一条 `define` statement 改为单行并以分号结束，方便人工 review。
- PROC REPORT 列宽改为 `style(column)=[cellwidth=...%]`，不再生成 character `width=`；Python Generator 根据 metadata/inferred length、format width 和 label length 加权分配，总计不超过 99%。
- PROC REPORT 的所有排序列按 Sort priority 放在 `column` statement 最前面并强制使用 `order order=data`；隐藏排序列额外使用 `noprint`，参与报告排序但不占显示宽度。
- 进入 PROC REPORT 前，Generated SAS 先按完整 Sort priority 执行 `PROC SORT`，并以 `_listing_row` 作为稳定 tie-breaker；PROC REPORT 读取该排序结果。
- Builder 的 Sort priority 改为可直接输入 `1–999` 的数字文本框，空白表示不排序。
- Column row 的操作按钮改为 `Up / Down / Remove`，并增加英文 tooltip；仍保持每个 Column 一行。
- SAS Generator regression fixture 扩展为至少 6 个 visible columns，并覆盖：
  - numeric → character：`PUT(ADY, 8.)`
  - character → numeric：`INPUT(AESTDTC, E8601DA.)`
  - variable combination：`CATX(' / ', AESTDTC, AETERM)`
  - datetime conversion：`PUT(ADTM, DATETIME20.)`
  - merged ADSL variable：`TRT01A`
  - multi-key sort：Subject、converted date、hidden numeric sequence DESC

本记录只描述已落地行为，不表示第 3–7 节中的其他审查问题已经完成。

本轮实施后自动化回归：

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
Ran 262 tests
OK
```

## 2. 当前已经可靠的部分

以下结构已经成立，后续不建议重写：

- Listing 已有独立的 model、expression、engine、result store、configuration、controller、Builder 和 Jinja2 SAS template。
- Builder source 固定到首次打开时的数据集，只有用户按 Clear 才释放；source 暂时不可用时不会清空输入。
- Merge ADSL 采用 source 为主表的 LEFT JOIN；ADSL BY 不唯一会阻止运行。
- Keep / Drop、重复变量 Ignore / Rename、rename collision 和 reserved internal names 已有基础校验。
- Data Filter 在 ADSL merge 后执行，因此可以引用合并得到的 ADSL 变量。
- Filter 复用 Viewer 的 SAS-like parser / AST，没有引入第二套 WHERE 语法。
- 表达式结果保持推断类型；`In Report = No` 的 numeric sort/check column 不会被强制转换为字符。
- 所有 Listing columns 都保存在 Viewer Result，便于 QC checking；普通隐藏列不进入 PROC REPORT，隐藏排序列以 `ORDER NOPRINT` 进入 PROC REPORT，确保层级顺序但不显示。
- 没有 Sort 时保留 source order；有 Sort 时 `_listing_row` 是最终稳定 tie-breaker。
- Python result 使用会话临时 SQLite，生成 `listing_config.json`，关闭 Result 后按现有 temp lifecycle 清理。
- SAS Generator 使用 Jinja2，代码结构清楚，最终 output dataset 不会被 cleanup 删除。
- PROC REPORT 使用单行 `column`/`define` statement 和 metadata-driven 百分比 cell width，所有 visible columns 合计不超过 99%。
- PROC REPORT 的排序列严格按 Sort priority 排在其他显示列之前；隐藏排序列不参与 99% 显示宽度分配。
- Sort priority 可以直接键入数字；Column 操作按钮具有明确文本和 tooltip。
- Merge Result 可以运行 Python Listing，但 SAS Generator 明确禁用，避免对不存在的物理 SAS source 生成错误代码。

当前定向基线：

```text
.venv/bin/python -m unittest \
  tests.test_listing \
  tests.test_listing_builder_state \
  tests.test_listing_sas_codegen \
  tests.test_analysis_controller

Ran 35 tests
OK
```

静态检查：

```text
.venv/bin/ruff check clinical_data_viewer/listing \
  clinical_data_viewer/ui/listing_builder.py \
  clinical_data_viewer/controllers/analysis/listing.py \
  clinical_data_viewer/codegen/sas/listing_generator.py \
  tests/test_listing.py \
  tests/test_listing_builder_state.py \
  tests/test_listing_sas_codegen.py

All checks passed!
```

这些结果只证明现有测试通过，不代表下面列出的边界已被覆盖。

## 3. 已确认的正确性问题

### P0-1：字符串字面量 `'-'` / `'+'` 会被误判为一元运算符

类型：表达式 parser 正确性  
涉及：`listing/expressions.py`

#### 当前行为

`ExpressionParser.factor()` 先按 `token.value in {"+", "-"}` 判断一元运算，再判断 token 是否为 string。因此常见表达式：

```text
AETERM || '-' || AEDECOD
CATX('-', AETERM, AEDECOD)
```

会把字符串 `'-'` 当成一元负号，最终报：

```text
Expected a variable, value, or function.
```

#### 建议实现

在 `factor()` 中先根据 token kind 处理 `string` / `number`，仅当 `token.kind == "op"` 时才解释 `+`、`-` 为一元运算符。

不要通过预处理表达式文本规避；必须修 parser 的 token-kind 判定。

#### 必须新增测试

- `A || '-' || B`
- `A || '+' || B`
- `CATX('-', A, B)`
- 数值一元 `-N`、`+N` 仍正常
- JSON AST 与 SAS renderer 保留正确的字符串 literal

---

### P0-2：表达式只有语法解析，没有统一的语义校验

类型：运行时崩溃 / Python-SAS contract  
涉及：`listing/expressions.py`、`listing/engine.py`、`listing/configuration.py`、`codegen/sas/listing_generator.py`

#### 当前行为

以下输入可以通过 parser，但在 Python evaluation 或生成 SAS 时才失败，或者两端含义不同：

- `STRIP()`：通过 parser，运行时 `IndexError`。
- `A + B`，其中 A/B 为 character：被推断为 numeric，但 Python 可能执行字符串拼接，SAS numeric arithmetic 则不等价。
- `COALESCE(A, B)` 接收 character：Python 可以返回字符串，SAS `COALESCE` 要求 numeric。
- `COALESCEC(N, A)` 接收 numeric：Python 可能返回 raw numeric，SAS renderer 会对 numeric 做字符转换。
- `SUBSTR()`、`SCAN()` 的参数数量、index/length 类型与合法范围没有在 Run 前统一校验。
- `CATS()`、`CATX()` 的最少参数没有固定 contract。

#### 建议实现

在 `listing/expressions.py` 新增唯一入口，例如：

```python
validate_expression_ast(expression, metadata) -> ExpressionSemantics
```

返回或确认：

- output kind
- inferred character length
- referenced variables
- function arity
- argument kinds
- supported format/informat
- division post-process 是否适用

规则至少包括：

- `+ - * /` 和 unary `+ -`：numeric only。
- `STRIP / UPCASE / LOWCASE`：恰好 1 个可字符展示的参数。
- `SUBSTR`：2 或 3 个参数；start/length 为 numeric；start > 0，length > 0（literal 时可提前检查）。
- `SCAN`：2 或 3 个参数；index 为 numeric；第一参数按 character 解释。
- `COALESCE`：至少 1 个 numeric 参数，全部 numeric。
- `COALESCEC`：至少 1 个 character 参数；如决定允许 numeric 自动显示转换，必须让 Python 与 SAS 使用同一转换规则并写入测试，否则 v1 应拒绝 numeric。
- `PUT / INPUT`：严格 2 个参数并校验 format/informat allowlist。
- `CATX`：至少 delimiter + 1 个 value；`CATS` 至少 1 个 value。

三个调用方必须复用同一 validator：

1. Builder/Controller Run 前校验。
2. Python Engine 执行前校验。
3. JSON builder / standalone SAS Generator 校验外部 configuration AST。

不要在每个调用方复制一份 arity/type 表。

#### 错误显示要求

错误应是可读的 `ListingExpressionError` / `ValueError`，包含 column output name 和具体表达式。例如：

```text
Column RATIO: COALESCE() accepts numeric arguments only.
```

不要向用户暴露 `IndexError`、`TypeError` traceback 作为主错误。

#### 必须新增测试

- 每个支持函数的最少/最多参数
- numeric/character 错配
- unary/binary arithmetic kind 校验
- `COALESCE` / `COALESCEC`
- `SUBSTR` start/length 边界
- invalid AST 直接交给 SAS Generator 时也被拒绝

---

### P0-3：CATX 对 whitespace missing 的处理与 SAS 不一致

类型：Python-SAS parity  
涉及：`listing/expressions.py`

#### 当前行为

Python `_missing()` 只把 `None` 和 `""` 当 missing。CATX 遇到 character value `"   "` 时：

1. `_missing(raw)` 返回 False；
2. `value.strip()` 变成空字符串；
3. delimiter 仍被保留。

已复现：

```text
CATX('|', 'A', '   ', 'B')
Python result: A||B
SAS CATX expected: A|B
```

#### 建议实现

不要全局擅自把所有 whitespace string 改成 missing，因为这会改变 Filter、Merge 和其他模块 contract。

只为 CATX 建立明确的 SAS blank-argument helper：character 参数 `strip() == ""` 时跳过；numeric `None` 时跳过。CATS 与 `||` 保持各自现有空白语义，不要混成同一个 operator。

#### 必须新增测试

- `None`、`""`、`"   "` 均不产生额外 delimiter
- 连续多个 missing arguments
- numeric missing
- CATS、CATX、`||` 的差异 fixture
- 同一 AST 的 Python 结果与 generated SAS intention 对照

---

### P0-4：CATX derived character length 少计算 delimiter

类型：SAS 输出截断风险  
涉及：`listing/expressions.py`、`codegen/sas/listing_generator.py`

#### 当前行为

`infer_length()` 对 CATX 只增加一次 delimiter length。实际 N 个 value 最多需要 N-1 个 delimiter。

已复现：三个长度 10 的变量与一个长度 1 的 delimiter，当前推断 31；安全上限应为 32：

```text
10 + 10 + 10 + 1 * (3 - 1) = 32
```

Python SQLite 不强制 character length，因此 Python Result 看起来正常；generated SAS 的 `_lst_valN $31` 可能截断，属于隐蔽 parity bug。

#### 建议实现

CATX length：

```text
sum(value display lengths)
+ delimiter display length * max(value_count - 1, 0)
```

继续使用现有 32767 cap。长度推断必须由 Python metadata 和 SAS Generator 共用，不能在 template 中另算。

#### 必须新增测试

- 2、3、5 个 value arguments
- 多字符 delimiter
- metadata length 缺失时的 200 fallback
- 超过 32767 时 cap
- generated SAS `length _lst_valN $...` 与 inferred metadata 一致

---

### P0-5：Column Format 未做安全且统一的 token 校验

类型：generated SAS 有效性 / 配置安全边界  
涉及：`listing/models.py`、`listing/expressions.py`、`codegen/sas/listing_generator.py`

#### 当前行为

Builder 中的 Format 会原样进入：

```sas
format variable <user text>;
define variable / format=<user text>;
```

当前没有统一验证 Format 是否为单个 SAS format token。错误文本会在生成 SAS 后才暴露，含分号的文本还可能破坏生成程序结构。

#### 建议实现

新增 shared SAS format token validator，允许项目确认支持的形式，例如：

```text
DATE9.
YYMMDD10.
E8601DT19.
8.2
$CHAR20.
```

拒绝：

- 分号
- 注释符
- 多个 statement token
- 空格连接的额外 SAS code
- 不合法 width/decimal

尽可能校验 output kind 与 format compatibility。对于 source metadata 中 Reader 提供、但 Python formatter 暂不支持的合法 SAS format，应保留 raw metadata，但要明确 Viewer display parity 的限制；不要把它当成任意用户输入代码。

#### 必须新增测试

- 合法 numeric/date/datetime/time/character formats
- `8.; run;`、comment、multiple tokens 被拒绝
- invalid Builder format 在 Run/Generate 前提示
- direct JSON 调 Generator 也被拒绝

## 4. 性能与后台执行问题

### P1-1：大型 Listing 会完整驻留 Python RAM

类型：性能 / 内存 / 大数据稳定性  
涉及：`listing/engine.py`、`listing/result_store.py`

#### 当前行为

Engine 当前流程：

```text
SELECT filtered rows
→ evaluate all expressions
→ append 全部 rows 到 Python list
→ Python 多轮 stable sort
→ 逐行写 Result SQLite
```

每一行同时保存 output tuple、source row 和 sort keys。几十万或上百万记录时，内存会明显放大；运行中取消也只能在阶段性 `worker.report()` 时生效，row loop 本身没有取消检查。

#### 建议实现：SQLite staging pipeline

保持 expression evaluator 不变，只替换 materialization/sort：

1. 在 Result 临时 SQLite 中创建 `listing_stage`：
   - `_listing_row`
   - 所有 typed derived columns
2. 以 `fetchmany()` 批量读取 filtered base rows。
3. Python 逐批计算 expression，并用 `executemany()` 插入 staging。
4. 每 N 行：
   - 检查 cancellation callback
   - 上报 processed/estimated progress
   - commit 或使用受控 transaction/savepoint
5. 用 SQLite `ORDER BY` 读取 staging：
   - configured sort columns
   - 每个 ASC/DESC 保持当前 missing order contract
   - `_listing_row` 为最终 tie-breaker
6. 流式写入最终 `dataset`，完成后删除 staging。

#### 关键边界

- 当前 Python ASC 是 missing first，DESC 是 missing last；SQLite ORDER BY 必须加显式 null-rank，不能依赖不同数据库默认值。
- Character sort 当前是 raw、case-sensitive；除非另开业务变更，不要在性能重构中改成 case-insensitive。
- 没有 Sort 时严格保持 `_listing_row` source order。
- 发生错误/取消时，Result directory 必须由现有 abort lifecycle 完整删除。
- 第一版不把 expression evaluation 翻译成 SQLite SQL，避免建立第三套 expression renderer。

#### 必须新增测试

- 10 万行 synthetic fixture 的 bounded-memory/完成测试（可标记 performance/integration）
- 多 key ASC/DESC、missing、相同值 stable order
- no-sort source order
- batch boundary 前后顺序一致
- 中途 cancellation 不留下 result/temp database
- Engine 结果与改造前小 fixture 完全一致

---

### P1-2：Run 前 warning 查询在 UI thread 执行

类型：UI 响应性  
涉及：`controllers/analysis/listing.py`、`listing/engine.py`

#### 当前行为

`ListingController.run_listing()` 在提交后台 worker 前同步调用 `ListingEngine.warnings()`。启用 ADSL merge 后，该方法对 source 和 ADSL 各执行一次 `count(*)` missing-BY 查询。大数据集上可能造成点击 Run 后界面短暂冻结。

#### 建议实现

把数据库型 preflight 放入 worker：

```text
Run
→ UI thread: metadata/config/expression/filter validation
→ background: missing BY counts + ADSL uniqueness preflight
→ UI thread: 一次性 Warning dialog（Continue / Cancel）
→ Continue: 使用同一 frozen config/handles 启动计算
```

如果为了避免两次 worker 调度，也可以在同一个 worker 中先返回 preflight outcome，再由 controller 继续提交正式 run；但不要在 worker thread 直接打开 QMessageBox。

Code Generator 保留 generated SAS 中的 ADSL uniqueness runtime guard。可在本地后台做提前检查，但不能删除可复用 SAS 程序的 runtime guard。

#### 必须新增测试

- warning query 不在 UI thread 执行
- 有 warning 时 Continue 才开始 Engine
- Cancel 不创建 result、不改变 Builder config
- busy state 在 preflight、dialog、run、fail/cancel 后恢复
- source/ADSL tab 在整个 preflight/run 期间均被 close blocker 保护

---

### P1-3：Worker cancellation 没有进入 Engine row loop

类型：用户取消 / 生命周期  
涉及：`listing/engine.py`、`controllers/analysis/listing.py`、`workers.py`

#### 建议实现

不要让 Engine 直接依赖 Qt Worker class。扩展 Engine API：

```python
run(..., progress=None, cancelled=None)
```

或传入轻量 callbacks/protocol。Controller 传入 `worker.report` 与 `lambda: worker.cancelled`。Engine 每个 batch 检查取消状态并抛出项目现有的可识别 cancellation exception。

这项应与 SQLite staging 一起实施，避免重复改 row loop。

## 5. Builder UI 与配置可用性

### P1-4：Add Variable 只能看到 source，不能看到 resolved ADSL variables

类型：核心可用性  
涉及：`ui/listing_builder.py`、`controllers/analysis/listing.py`、`listing/engine.py`

#### 当前行为

启用 ADSL merge 后，Filter 和 expression 可以手工输入 ADSL 变量，但 `+ Add Variable` picker 仍只来自 source metadata。用户无法直接选择 `TRT01A`、`SAFFL` 或 `AGE_ADSL`，也看不到 duplicate policy 之后的真实 output name。

#### 建议实现

1. 把“预览 resolved metadata”的职责保留在 `ListingEngine.resolved_metadata()`，不要在 UI 复制 Keep/Drop/rename 规则。
2. Builder merge 相关字段变化时发出轻量 `merge_preview_changed` signal。
3. Controller 收集当前 source/ADSL/config，调用 resolved metadata validation。
4. Builder 接收 resolved picker items，并分组或用 tooltip 标识：
   - Source
   - ADSL
   - Renamed ADSL，例如 `AGE → AGE_ADSL`
5. merge config invalid 时保留现有用户输入，picker 暂停刷新，并在 merge group 下显示 inline error。

不要自动向 columns table 添加 ADSL 变量，也不要在切换 Keep/Drop 时删除用户已经建立的 column；只标记该 expression 当前无效。

#### 必须新增测试

- Merge off：仅 source variables
- Merge on：显示 selected ADSL variables
- Keep/Drop 改变后 picker 刷新
- Ignore duplicate 不出现 ADSL duplicate
- Rename duplicate 出现 resolved name
- invalid rename collision 时不清空 columns

---

### 已完成-1：PROC REPORT 使用百分比列宽

类型：PROC REPORT UI  
涉及：`ui/listing_builder.py`、`ListingBuilderSelection`、controller、models、configuration、tests

Generator 已改为：

```sas
define USUBJID / order order=data style(column)=[cellwidth=12.4%] 'Subject';
```

Visible columns 按显示字符需求加权分配，总计固定为 99%。权重来自：

- character column：metadata length 或 derived expression inferred length
- numeric column：SAS format width；没有 format 时按 12 characters
- column label：如果比内容估算更长，则使用 label length
- 防止极端 metadata 独占页面：用于权重的 character estimate 限制在 8–80
- 每列先保留最小可读百分比，再按上述权重分配剩余空间

该算法只在 Python Generator 中执行，不写入 generated SAS。SAS 只接收最终的 `cellwidth=...%`。`report.line_size` 仍保留在 JSON 并用于现有 `options linesize=`，但不再用于计算每列宽度。

后续只需确认两个 contract，不需要重新实现 character width：

- JSON v1 的 `width_percent=0` 明确表示 auto allocation，`report.width_method` 为 `metadata_weighted_visible_columns`。
- `line_size` 是保留的 SAS overall option，不是 column-width input；如未来废弃，应通过 JSON v2 或兼容迁移处理。

已新增测试覆盖 3 列、6 列和超长 character column，确认总计为 99%、长列获得更大百分比，且 generated PROC REPORT 不包含 character `width=`。

---

### P1-6：Column row 缺少就地 validation feedback

类型：降低用户失误率  
涉及：`ui/listing_builder.py`、expression validator

#### 建议实现

保留当前“一行一个 Column”的紧凑布局，不改成纵向大表单。增加：

- `Validate` 按钮，或 300–500 ms debounce validation。
- 无效 row 的 Expression/Output/Format cell 使用淡红边框，而不是修改整行底色。
- status 显示第一条简洁错误；tooltip 显示完整原因。
- 可选增加只读 tooltip：inferred type、inferred length、source/derived。

不要在每次键入字符时弹 QMessageBox。Run 和 SAS Code Generator 仍执行最终 authoritative validation。

---

### P2-1：Clear 没有重置全部 Listing 配置

类型：状态一致性  
涉及：`ui/listing_builder.py`

#### 当前行为

Clear 会清空 filter、columns、merge enabled、Keep/Drop 和 rename map，但不会明确重置：

- BY variable（用户可能已改成其他值）
- duplicate policy
- ADSL selector
- line size（增加 UI 后）

#### 建议实现

明确 Clear contract：

```text
BY = USUBJID
Duplicate policy = Ignore
Merge disabled
Keep/Drop/Rename empty
Filter empty
Columns empty
Line size = 132
Source released
```

ADSL selector可以保留应用级最近选择，或重置为自动识别的 ADSL；两者会影响用户体验。建议保留 selector，但由于 Merge 已关闭，它不属于有效 calculation config。必须通过测试固定这个决定。

---

### P2-2：手工 Rename map 容易在 ADSL/Keep/Drop 改变后变陈旧

类型：UI 状态  
涉及：`ui/listing_builder.py`

#### 当前行为

用户一旦编辑 rename map，`_rename_map_user_edited` 持续为 True。之后更换 ADSL 或 Keep/Drop 时，旧 map 保留，可能直到 Run 才提示 unknown/collision。

#### 建议实现

增加 `Reset Auto`，或把 rename map 改为小型 duplicate-resolution table：

```text
ADSL variable | Resolution | Output
AGE           | Rename     | AGE_ADSL
SEX           | Ignore     |
```

第一轮可采用最小方案：

- 增加 Reset Auto
- ADSL changed 时显示“rename map needs review” inline warning
- 实时验证 source collision、duplicate targets、reserved names
- 不静默覆盖用户 map

## 6. Python-SAS parity 与 Generator 加固

### P1-7：SCAN 的 Python 行为与 SAS SCAN 不完全一致

类型：跨语言 parity  
涉及：`listing/expressions.py`、SAS renderer、tests

Python 当前使用 `str.split(delimiter_string)`；SAS SCAN 的第三参数是 charlist，每个字符都是 delimiter，且默认 delimiter 集、连续 delimiter 处理也不同。

建议 v1 明确采用 SAS SCAN 的受限 contract，并让 Python evaluator匹配：

- 2 参数：固定项目支持的 SAS-compatible default delimiters。
- 3 参数：第三参数按 charlist 处理。
- 连续 delimiters 跳过 empty words，除非未来支持 modifiers。
- index 从 1 开始；超出返回空字符串。
- v1 不支持 SCAN modifiers。

必须测试 comma+space charlist、连续 delimiters、默认 delimiter、out-of-range。

---

### P1-8：ANYDTDTE / ANYDTDTM 名称暗示的范围大于 Python 实际支持范围

类型：跨语言 parity  
涉及：`listing/expressions.py`、tests、用户文档

Python 当前主要处理 ISO-like text，而真实 SAS ANYDTDTE/ANYDTDTM 接受更广输入。generated SAS 可能成功，但 Python reference result 失败。

建议先固定 v1 支持矩阵：

- 如果只保证 ISO，UI/错误信息必须明确“Listing v1 supports ISO forms for ANYDTDTE/ANYDTDTM”。
- 如果要对齐 SAS，逐个增加经过测试的输入形态；不要调用 locale-dependent 模糊猜测。

必须有 Python fixture 与真实 SAS acceptance fixture；没有真实 SAS 环境时不得声称完全 parity。

---

### P1-9：Standalone SAS Generator contract validation 不完整

类型：JSON contract 防御  
涉及：`codegen/sas/listing_generator.py`

通过正常 Builder 生成的 JSON 已经过一部分 validation，但 Generator 是独立入口，应拒绝手工修改或未来加载的非法 JSON，而不是 silent fallback。

需要补齐：

- `calculation.reference_engine == python_listing_v1`
- `sort.stable_tie_breaker == _listing_row`
- `report.width_method == metadata_weighted_visible_columns`
- merge `by` 恰好一个变量
- Keep / Drop 不可同时存在
- duplicate policy 枚举
- source/ADSL 均存在 BY，且 kind 兼容
- rename targets case-insensitive unique、不与 source/output collision、不使用 reserved name
- columns output case-insensitive unique
- sort order positive/unique，direction enum
- report type enum
- expression AST 通过第 3 节的 shared semantic validator
- output dataset、libref、member 能安全渲染

不要修改 JSON v1 字段名；这是 validator hardening，不是 JSON v2。

---

### P1-10：SAS dataset reference 没有统一使用 name/libref helper

类型：generated SAS 兼容性  
涉及：`listing_generator.py`、`listing.sas.j2`、shared SAS helpers

变量名已经用 `sas_name`，但 template 中 source member、ADSL member、source/adsl libref 不是同一套严格 helper。建议：

- libref 单独校验 1–8 characters，letter/underscore 开头。
- member 使用 `sas_name()` 或统一 dataset-reference renderer。
- 不把 filesystem display path 当 member。
- XPT 与 sas7bdat 分支继续保持当前 input contract。

必须测试 nonstandard member name、超长 libref、XPT member、合法 name literal。

---

### P2-3：JSON `width_percent=0` 的含义不明确

当前 generator 按 metadata/inferred character需求自动分配 `cellwidth=...%`，总计不超过 99%，但每个 column JSON 仍固定写 `width_percent: 0`。

为避免破坏 JSON v1，建议明确：

```text
width_percent = 0 means metadata-weighted automatic allocation by report.width_method
```

在 configuration 注释/README 与 Generator validation 中固定，不要把 0 解释为真实 0%。以后需要手工 width override 时再设计兼容扩展。

---

### P2-4：ADSL BY 的 SQLite/SAS character equality 边界需固定

SQLite text equality与 SAS fixed-character comparison 对 trailing blanks、collation 的行为可能不同。临床 USUBJID 通常已标准化，但 Python reference 与 SAS merge 应有 fixture 明确 contract。

第一轮不建议自动 trim/casefold BY，因为这会改变主业务语义。先增加：

- trailing blanks
- case difference
- empty/NULL
- numeric BY

测试并记录实际结果；如需归一化，应另开跨模块业务决策。

## 7. 推荐施工顺序

### Phase 1：锁定表达式 contract 并修 P0 correctness

涉及文件：

- `listing/expressions.py`
- 新增建议：`tests/test_listing_expressions.py`
- `tests/test_listing.py`
- `tests/test_listing_sas_codegen.py`

步骤：

1. 新增 expression semantic validator 与 function signature table。
2. 先用 failing tests 锁住 `'-'/'+'` literal、invalid arity/type、CATX whitespace、CATX length。
3. 修 parser token-kind 顺序。
4. 修 CATX blank filtering 与 length。
5. 让 `parse_expression()` 后的 Controller、Engine、configuration builder、Generator 共用 validator。
6. 保持 JSON AST node names 不变。
7. 运行 Listing 定向测试和所有 Filter/SAS renderer regression tests。

完成标准：非法表达式在 Run/Generate 前给出同一类用户错误；合法表达式 Python/SAS fixture 一致。

### Phase 2：Format 和 Generator contract hardening

涉及文件：

- `listing/models.py`
- `listing/configuration.py`
- `codegen/sas/listing_generator.py`
- `codegen/sas/templates/listing.sas.j2`
- shared SAS name/format helper（如新增，应供其他 generator 复用）

步骤：

1. 增加 SAS format token validator。
2. 增加 standalone JSON v1 validator；不改 schema。
3. 统一 libref/member/output dataset rendering。
4. 固定 `width_percent=0` 为 auto 的含义。
5. 不改现有 Jinja2 program layout和最终 output fields。

完成标准：正常 Builder JSON 生成的 SAS 结构不变；非法/篡改 JSON 明确拒绝。

### Phase 3：Builder UI 可用性

涉及文件：

- `ui/listing_builder.py`
- `controllers/analysis/listing.py`
- `listing/models.py`
- `listing/configuration.py`
- UI state/controller tests

步骤：

1. 增加 resolved merged metadata picker。
2. 保留已完成的数字 Sort priority，以及 `Up / Down / Remove` action buttons。
3. 增加 non-modal row validation feedback。
4. 增加 rename Reset Auto / stale warning。
5. 完整定义 Clear reset state。
6. 保留现有一行一个 Column 的横向紧凑布局和上下/左右滚动能力。

完成标准：用户无需手输即可选到 ADSL/renamed variable；宽列报表可在 UI 内调整；错误不等到 Run 才发现。

### Phase 4：后台 preflight 与取消

涉及文件：

- `controllers/analysis/listing.py`
- `listing/engine.py`
- controller/UI tests

步骤：

1. 把 missing-BY count 与 ADSL uniqueness preflight 移出 UI thread。
2. 建立 Continue/Cancel warning flow。
3. 增加 Engine cancellation callback contract。
4. 确认 preflight/run 全程 retain source/ADSL 并阻止关闭。

完成标准：大 source/ADSL 点击 Run 后主界面仍响应，Cancel 不产生临时结果。

### Phase 5：SQLite staging 与大数据验收

涉及文件：

- `listing/engine.py`
- `listing/result_store.py`
- performance/integration tests

步骤：

1. 引入 staging table 和 batch insert。
2. 把最终排序改为显式 SQLite ORDER BY + null rank + `_listing_row`。
3. 接入 batch progress/cancellation。
4. 确保 finish/abort/temp cleanup 原子性。
5. 对比改造前后小 fixture 的完整 rows/metadata/JSON。

完成标准：大 Listing 不再把完整结果保存在 Python list；排序、missing、stable order 和 output schema不变。

### Phase 6：SCAN / INPUT parity 与真实 SAS acceptance

步骤：

1. 固定 SCAN v1 charlist contract并实现 Python parity。
2. 固定 ANYDTDTE/ANYDTDTM支持矩阵。
3. 生成一组真实 SAS 程序，分别验证 sas7bdat/XPT、ADSL merge、INPUT/PUT/CATX/SCAN、numeric hidden sort、division by zero。
4. 将 SAS output 与 Python Listing Result 逐列对比。

## 8. 测试与回归验收清单

### 8.1 自动化测试

至少新增/更新：

- `tests/test_listing_expressions.py`
  - parser、arity、kind、CATX/CATS/SCAN、INPUT/PUT、length inference
- `tests/test_listing.py`
  - Python engine、merge、filter、sort、missing、batch/cancel
- `tests/test_listing_builder_state.py`
  - fixed source、Clear、line size、resolved ADSL picker、rename state
- `tests/test_listing_sas_codegen.py`
  - JSON validation、format safety、dataset refs、Python-SAS expression fixtures
- `tests/test_analysis_controller.py`
  - background preflight、warning Continue/Cancel、busy/close blocker/result lifecycle

每一阶段先跑相关 tests，交付前必须运行：

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check clinical_data_viewer tests
```

如果全量测试出现 ResourceWarning、Qt warning 或偶发 worker failure，必须区分既存问题与本次新增回归，不能只报告“定向测试通过”。

### 8.2 Python 功能验收

- Source only listing
- Source + ADSL left merge
- Keep / Drop / Ignore / Rename
- ADSL duplicate BY blocked
- source/ADSL missing BY warning
- Filter 引用 source 与 ADSL variable
- 直接变量、concat、CATS、CATX、STRIP、UPCASE、LOWCASE、SUBSTR、SCAN、COALESCE、COALESCEC、INPUT、PUT、arithmetic
- numeric hidden sort column 在 Viewer 中仍可见且保持 numeric
- multi-key ASC/DESC、missing、ties、no-sort
- result JSON 与 temp cleanup
- Merge Result可运行 Python Listing、SAS button disabled

### 8.3 Windows UI 验收

- Listing Generator 从 Tools 打开，source固定到启动时 Dataset。
- 切换 dataset tab 不改变 Builder source。
- source Tab 关闭时出现警告；Clear 后可以关闭。
- Builder 窄宽度下水平滚动可看到 Actions；垂直滚动可看到全部页面与底部按钮。
- ADSL Browse/加载后 picker 刷新，用户输入不丢失。
- 大数据 preflight/run期间窗口可移动、可切换 Tab、可取消。
- Result 中所有 columns 可供 Filter、WHERE、Sort、Select Columns、CSV export 使用。

### 8.4 真实 SAS 验收

至少覆盖：

1. `.sas7bdat` source，无 ADSL。
2. `.xpt` source。
3. ADSL LEFT MERGE + Keep。
4. ADSL duplicate Rename。
5. Filter 引用 ADSL variable。
6. CATX whitespace/missing。
7. INPUT E8601DA/E8601DT + output format。
8. PUT DATE9./DATETIME20./TIME10./8.2。
9. hidden numeric multi-sort。
10. division denominator=0 的 post-process。
11. many visible columns + custom line size。

验收时比较：

- record count
- row order
- raw numeric values
- character values
- labels/formats
- PROC REPORT column order和隐藏列行为

没有真实 SAS 实跑结果前，只能声明 generated SAS 通过文本/contract tests，不能声明与 Base SAS 完全一致。

## 9. 本轮明确不做

- 不重写 Listing 整体架构。
- 不改变 JSON v1 顶层字段名。
- 不允许 derived column 引用另一个 derived column。
- 不增加 CASE WHEN 或任意 SAS/Python expression。
- 不实现 R Generator。
- 不实现 RTF/PDF、Title、Footnote、page break、spanning header、compute block。
- 不把隐藏列从 Viewer Result 删除。
- 不改变 ADSL merge 的 source-left 语义。
- 不把 character BY 自动 trim/casefold。
- 不把 character sort改为 case-insensitive，除非另行确认。
- 不修改其他 Analysis modules 以“顺便统一”。如需抽 shared helper，应保持其现有 tests 全绿。

## 10. 推荐优先级结论

建议下一轮先只实施 Phase 1：

1. 修 `'-'/'+'` string literal parser bug。
2. 建立 expression semantic validator。
3. 修 CATX whitespace parity。
4. 修 CATX length truncation。
5. 补完整 expression regression tests。

完成并全量回归后，再进入 Generator hardening 与 UI/性能阶段。这样可以先锁住最容易造成错误结果或生成错误 SAS 的部分，同时避免一次修改过多层级。
