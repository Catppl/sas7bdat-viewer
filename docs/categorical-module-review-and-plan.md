# Categorical Table 模块审查与实施计划

审查日期：2026-08-28  
审查基线：`c9007b9` (`Complete PROC MEANS filter reload lifecycle`)

## 1. 审查范围

本次只做代码审查、风险验证和施工规划，没有修改 Categorical 的业务代码。

重点检查：

- `clinical_data_viewer/categorical/models.py`
- `clinical_data_viewer/categorical/engine.py`
- `clinical_data_viewer/categorical/result_store.py`
- `clinical_data_viewer/categorical/drilldown.py`
- `clinical_data_viewer/ui/categorical_builder.py`
- `clinical_data_viewer/controllers/analysis/categorical.py`
- Categorical 相关 Engine、Builder、Controller、UI smoke tests

同时对照：

- Rule-based Table 的 missing、Population treatment 和 JSON contract
- AE Table 的 missing、Population treatment、Treatment 校验和 long result order
- PROC MEANS Builder 的固定 source / Reload 生命周期
- Listing、Rule-based、AE、Categorical 的固定 Builder source 管理

## 2. 当前实现中已经可靠的部分

以下能力已经落地，不建议在后续修复中重构：

- Builder source 固定到首次打开时的数据集，只有 Clear 才释放。
- 关闭固定 source Tab 前会给出警告并阻止关闭。
- Numerator WHERE、Population WHERE、Baseline WHERE、Postbaseline WHERE 独立编译和执行。
- Population N、Non-missing N、Baseline + Postbaseline n1 三种 denominator 已有独立计算路径。
- n1 明确限制为 record count，并按 treatment + context + subject 匹配 baseline/postbaseline。
- Total 由 Engine 重新计数，不依赖治疗组显示值相加。
- Wide Result、Long Result、cell map、numerator/denominator drill-down 已建立。
- Result 关闭不会清空 Builder；只有 Clear 才清空用户输入。
- Engine 在后台 worker 中执行，结果使用会话临时 SQLite。
- ADSL 与 source 临时目录在结果生命周期内 retain/release。

基线测试：

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
Ran 223 tests
OK
```

Categorical 定向测试也通过，但现有测试没有覆盖本文件第 3 节中的关键边界。

## 3. Categorical 更新建议

### P1-1：空字符串 Subject ID 被错误计入 distinct subject

类型：统计正确性  
涉及：`categorical/engine.py`

#### 当前行为

`_missing()` 已把 character `""` 识别为 missing，但 `_count_value()` 对 distinct subject 只判断：

```python
subject is not None
```

因此空字符串 `USUBJID=""` 会进入 distinct set。

本次最小诊断已复现：一个正常 Subject 加一个空字符串 Subject，在同一 level/treatment 下会得到 `freq=2`，而不是 `freq=1`；denominator 也会被放大。

#### 影响范围

- distinct-subject numerator
- Population N denominator
- Non-missing N denominator
- Total
- wide/long result 百分比
- Engine 与 drill-down Subjects 返回值不一致：Query Builder 已排除空字符串，但 Engine 没有排除

#### 建议实现

不要只在 `_count_value()` 中写死字符串判断。把 subject metadata 传入统一计数入口，复用与 Rule-based / AE 相同的 missing 语义：

```text
numeric subject: NULL 为 missing
character subject: NULL 或 "" 为 missing
```

纯空格 `"   "` 是否视为 missing 当前项目尚无统一 contract。第一轮保持现有项目语义，不擅自 trim；如需要与 SAS blank 完全一致，应单独确认后跨模块统一。

#### 必须新增测试

- numerator 同时包含 `"S1"`、`""`、`NULL`，只计 `S1`
- Population denominator 排除 `""`、`NULL`
- Non-missing denominator 排除 `""`、`NULL`
- Total 同样排除 missing subject
- Numerator Subjects / Denominator Subjects drill-down 行数与 Engine 的 distinct count 一致

---

### P1-2：0-frequency treatment cell 被显示为空白，而不是明确的 0

类型：结果完整性 / 临床表格可读性  
涉及：`categorical/engine.py`、`categorical/result_store.py`

#### 当前行为

某个 level 在 Treatment A 出现、在 Treatment B 未出现时，`_write_item()` 只写入实际存在于 numerator map 的 treatment。Treatment B 的 wide cell 为空字符串，long result 也没有该 treatment/level row。

例如现有测试已经把下面行为锁住：

```text
BLACK | 1 (50.0) | <blank> | 1 (33.3)
```

临床 n (%) table 更明确的结果通常应是：

```text
denom > 0  -> 0 (0.0)
denom = 0  -> 0 (—)
```

#### 建议实现

对每个已发现的 Item/Context/Level，遍历全部 resolved treatment columns，而不是只遍历 numerator 中已有的 key：

```text
frequency = numerator.get(..., 0)
denom = denominator.get(..., 0)
```

同时为 0-frequency cell 写入：

- wide display
- `categorical_long`
- `categorical_cell_map`

这样 0 cell 也可以 drill-down，并返回 0 条 numerator records/subjects。

#### 需要用户确认的显示决策

推荐采用上述显式 0 规则。如果业务上确实希望空白表示“0”，应先固定为 display contract，再实施；不要让 wide 与 long 使用不同规则。

#### 必须新增测试

- 一个 level 仅存在于 A，B denominator 大于 0：B 显示 `0 (0.0)`
- denominator 为 0：显示 `0 (—)`
- long result 包含 FREQ=0 的 treatment row
- 0-frequency cell map 存在，numerator drill-down 返回 0 行
- `percent_digits=0/1/2/4` 的 0 值显示

---

### P1-3：Population denominator 不能选择独立的 ADSL Treatment variable

类型：常见临床数据兼容性  
涉及：Builder、models、controller、engine、drill-down、tests

#### 当前行为

Categorical 只保存一个 `treatment_variable`，并要求 source 和 ADSL 都存在同名变量。

真实场景常见：

```text
Source ADAE: TRTA 或 TRTAN
Population ADSL: TRT01A 或 TRT01AN
```

Rule-based 和 AE Table 已支持 `population_treatment_variable`，Categorical 尚未跟进。

#### 建议实现

1. `CategoricalBuilderSelection` 增加：

   ```python
   population_treatment_variable: str
   ```

2. Population 页面增加紧凑的：

   ```text
   ADSL treatment variable [ TRT01A v ]
   ```

3. `DenominatorConfig` 增加同名字段，默认空字符串以兼容旧构造方式；为空时回退到 source treatment variable。
4. `CategoricalConfig.validate()` 分别验证：

   - source treatment 存在
   - population treatment 存在
   - 两侧 kind 兼容

5. Engine 的 Population treatment discovery 和 denominator grouping 使用 ADSL treatment variable。
6. Drill-down 在 Population denominator 下使用 ADSL treatment variable；numerator 仍使用 source treatment variable。
7. Result 的 treatment columns 仍用合并后的业务 value，不暴露变量名差异。

#### 注意事项

- 不按变量名自动猜测唯一答案；可参考 AE/Rule Builder 的默认选择策略，但允许用户明确选择。
- Character vs numeric treatment 不应静默转换后合并。
- Source 与 ADSL 中同一个业务 treatment value 必须采用相同 canonical JSON key。
- Context key 不应依赖两侧 metadata 中变量名的大小写。Source `PARAMCD` 与 ADSL `paramcd` 应使用同一 config/canonical context key，否则 numerator 与 denominator 可能无法关联。

#### 必须新增测试

- Source `TRTA` + ADSL `TRT01A`
- Source `TRTAN` + ADSL `TRT01AN`
- 两侧 kind 不一致时清晰报错
- treatment level discovery 合并两侧 level
- Population denominator drill-down 使用 `TRT01A/TRT01AN`，numerator 使用 `TRTA/TRTAN`
- Builder 切换 ADSL 后刷新可选变量且保留有效手工选择
- Source/ADSL context 变量只有大小写不同时，denominator 仍能正确匹配

---

### P1-4：字符型 missing Item level 会显示成空白行

类型：显示与聚合语义不一致  
涉及：`categorical/engine.py`、drill-down、tests

#### 当前行为

`include_missing_level=True` 时：

- `NULL` level 显示 `(Missing)`
- character `""` 被 `_missing()` 识别为 missing，但 `_canonical()` 保留为 `""`
- 最终 level label 是空字符串，只剩四个缩进空格

这会生成看起来没有名称的行。

#### 建议实现

为 hierarchy/item level 建立 metadata-aware canonicalization：

```text
如果 _missing(value, variable) 为 true：canonical level = None
否则保留 raw value，并继续保持 numeric JSON type
```

context 是否也把空字符串统一显示为 `(Missing)`应一起固定，避免 header 出现 `PARAMCD=` 的空白文本。

#### 必须新增测试

- character `NULL` 与 `""` 合并为一个 `(Missing)` level
- duplicate missing records 按当前 count type 正确计数
- missing level drill-down 同时命中 `IS NULL OR = ''`
- numeric missing 仍只匹配 NULL

---

### P1-5：Item label 没有应用到 Wide Result header

类型：已暴露 UI 配置未生效  
涉及：`categorical/engine.py`、tests

#### 当前行为

Builder 允许为每个 Item 输入 `Item label (optional)`，Long Result 也保存 `item_label`，但 Wide Result header 始终使用 `item.variable`：

```python
header = item.variable if not context_text else f"{item.variable} — {context_text}"
```

因此用户输入的 label 对主临床样式表没有任何可见效果。

#### 建议实现

使用：

```text
item_title = item.label or item.variable
```

Wide header、Long Result 和后续 traceability 都以同一 fallback 规则处理。Context 后缀仍附加到 `item_title` 后面。

#### 必须新增测试

- 自定义 Item label 显示在 Wide header
- label 为空时回退 variable name
- 带 context 时为 `Label — CONTEXT=value`
- Long Result 的 ITEM/ITEM_LABEL contract 与 Wide header 可追溯

---

### P1-6：固定 source Reload 生命周期只在 PROC MEANS 完整实现

类型：跨模块状态一致性  
涉及：`analysis_controller.py`、CategoricalController、CategoricalBuilder

#### 当前行为

MainWindow 已发送：

```text
source_reload_started
source_reload_completed
source_reload_failed
```

但 `AnalysisController` 当前只转发给 PROC MEANS。

如果 Categorical Builder 固定绑定的数据集 Reload：

- source Tab handle/metadata 已替换
- Builder 的变量 combo、Items、Context token 仍可能保留旧 schema
- 新增变量不会出现
- 已删除变量直到 Run/validate 才报错
- Reload 期间 Run 没有模块级锁定状态

#### 建议实现

参考 PROC MEANS 已完成的生命周期，但保持 Categorical 自己的输入 contract：

```text
Reload started
  -> Categorical Builder 标记 source reloading
  -> Run disabled，所有输入保留

Reload completed
  -> 重新绑定同一个 DatasetTab 的新 metadata
  -> 保留 Numerator WHERE、Population WHERE、Items 和 denominator 设置
  -> 裁剪已不存在的 Item / Context / treatment / subject / analysis value
  -> status 明确列出被移除的选择

Reload failed
  -> 原 cache 仍完整：恢复 Run，输入保持
  -> cache 不完整：继续禁用并提示重新 Reload
```

#### 关键实现细节

`CategoricalItemEditor.set_metadata(..., preserve=True)` 当前直接保留 `_configs`，没有裁剪无效 Item 和 Context。应让该方法返回 removed selections，供 Controller 在 status 中展示。

不要在 Reload 时重新继承 Dataset Tab 最新 WHERE。Builder 的 Numerator WHERE 在首次绑定后应保持独立。

#### 必须新增测试

- Reload started 禁用 Run 且不清空输入
- Reload completed 刷新 combo metadata
- 已删除 Item 被移除并报告
- 已删除 Context 被移除并报告
- 有效 Item/Context 和全部 WHERE 文本保留
- Reload failed 恢复状态
- source Dataset Tab 后续 WHERE 修改不覆盖 Builder Numerator WHERE

---

### P2-1：Builder 的空 Numerator WHERE 不是完全 authoritative

类型：防误操作 / Filter ownership

#### 当前行为

Builder 首次绑定时会继承 Dataset Tab WHERE，这部分正确。

但 Run 时，如果用户主动把 Numerator WHERE 清空，而 Dataset Tab 仍有 WHERE，会再次弹出：

```text
Numerator WHERE is empty. Use the current dataset WHERE for this calculation?
```

默认选择 Yes。这样“用户清空 Builder filter”并不稳定地代表“本次不筛选”。

#### 建议实现

与最新 PROC MEANS Filter ownership 对齐：

- 仅首次绑定 source 时继承 Dataset WHERE
- 后续 Builder editor 是唯一 authoritative filter
- 空字符串明确表示不应用 Numerator filter
- Run 不再重新读取或询问 Dataset Tab WHERE

需要增加 Controller test，确认 Run 直接使用 selection/editor snapshot。

---

### P2-2：Treatment missing 与 Treatment order 没有正式 contract

类型：统计输出稳定性 / 跨模块一致性

#### 当前行为

- `NULL` treatment 会生成 `(Missing)` treatment column。
- character `""` 会生成 label 为空的 treatment column。
- Treatment 排序使用 `str(value)`：numeric `1, 2, 10` 可能得到 `1, 10, 2`。
- 字符排序区分大小写。
- Rule-based / AE 默认在 missing treatment 时阻止计算，Categorical 行为不同但 UI 没有解释。

#### 建议决策

在实施前由用户选择以下 contract 之一：

1. **推荐：missing treatment = error**，与 Rule-based / AE 一致，降低临床表误分组风险。
2. missing treatment 作为正式 `(Missing)` column，但必须统一 `NULL`/`""`，并在 Builder 明确显示该策略。

Treatment order 建议建立 metadata-aware stable key：

- numeric：按 numeric value
- character：按 `casefold()` 后的 label/value，再用原值作稳定 tie-break
- missing（如允许）：固定最后

不要在未确认业务 contract 前只改排序代码。

---

### P2-3：`CategoricalItem.level_order` 存在但未生效

类型：死 contract / 可维护性

#### 当前行为

`CategoricalItem` 保存 `level_order`，Builder 也在编辑时原样保留，但：

- UI 没有配置入口
- Engine `_write_item()` 不读取它
- tests 没有定义其含义

#### 建议实现

第一阶段不要扩展复杂排序 UI。二选一：

- 若近期需要自定义 level order：定义完整 contract、UI、fallback 和 tests 后实施。
- 若近期不需要：标记为 reserved/deprecated，避免开发者误以为已支持；不要静默声称该字段有效。

同时明确默认 level sort：numeric、character、missing 的顺序。当前按 JSON 字符串排序不是理想的业务 contract。

---

### P2-4：Long Result 依赖插入顺序，没有显式 row/treatment order

类型：结果稳定性

#### 当前行为

`categorical_long` 没有 `row_order` / `trt_order`，Long Result 通过：

```sql
ORDER BY rowid
```

获得当前顺序。当前 writer 恰好按循环顺序插入，但这不是正式 schema contract。

#### 建议实现

增加内部 traceability 字段：

```text
item_order
level_order
trt_order
```

Long Result 明确：

```sql
ORDER BY item_order, level_order, trt_order
```

Total 固定最后。字段可在 Long Result 中显示，也可仅作为内部排序列，但含义必须测试固定。

---

### P2-5：多 Item 时会重复扫描 source / population

类型：性能

#### 当前行为

每个 Item 至少执行：

- 一次 numerator 全量查询
- 一次 denominator 查询

Population / Non-missing denominator 即使相同 treatment/context 组合，也会随 Item 重算。Item 增多时 I/O 近似线性放大。

#### 建议实施边界

先修正确性，再基于真实大数据 benchmark 决定优化，不立即重写为 pandas pipeline。

可选的低风险优化顺序：

1. 按 denominator type + context tuple 缓存 denominator。
2. 同一 source scan 中只 select 必需列。
3. 对重复 context contract 的 Items 合并 SQLite GROUP BY 查询。
4. 保持 SQLite 流式读取，不把完整数据集装进 pandas/DataFrame。

验收必须比较优化前后 wide、long、cell map 和 drill-down 结果完全一致。

---

### P3：Categorical 尚无稳定 configuration JSON

类型：后续扩展 / 非当前缺陷

Rule-based、AE、PROC MEANS、Listing 已有可保存的配置 contract，Categorical 目前只有运行时 dataclass 和 long traceability table。

此前需求已明确“Categorical JSON 以后再做”，因此本项只记录，不纳入前两阶段施工，也不应顺带实现 SAS/R generator。

未来如启动，应先冻结 Python semantics，再定义：

- input / variables
- numerator filter text + AST
- items / contexts / missing-level policy / level order
- count contract
- treatment 与 population treatment
- 三种 denominator 的独立 filters
- total / percent / rounding / zero display
- resolved treatment/level snapshot 的定位

## 4. 其他模块与共性问题记录

本节是审查 Categorical 时发现的跨模块事项，不代表本轮应同时修改。

### Cross-1：Reload lifecycle 目前只转发给 PROC MEANS

受影响 Builder：

- Listing
- Rule-based
- AE Table
- Categorical

它们都采用“固定 source，Clear 才释放”的新 contract，但 Reload started/completed/failed 只有 PROC MEANS 收到通知。建议先用 Categorical 完成模板，再逐模块迁移，不能一次性泛化后未经各模块 selection-pruning tests 就上线。

每个模块的裁剪规则不同：

- Listing：Column expressions、sort、ADSL merge selections
- Rule-based：Row filters、treatment、subject、population treatment
- AE：SOC/PT/treatment/subject、population treatment
- Categorical：Items、contexts、denominator values

### Cross-2：Missing helper 在 Rule-based、AE、Categorical 重复实现并已发生语义漂移

目前三个 Engine 各自实现 `_missing()` / `_missing_sql()`。Categorical 已出现“helper 知道空字符串 missing，但 distinct counter 不使用 helper”的实际 bug。

建议在各模块回归测试补齐后，再抽取小型的 metadata-aware helper；不要先做大规模基础设施重构。共享范围只应包含：

- scalar missing 判断
- SQLite missing / not-missing predicate
- canonical scalar value（保留 numeric JSON type）

不要把各模块特有的 hierarchy、treatment policy 或 denominator 逻辑塞进 shared helper。

### Cross-3：Treatment ordering contract 跨模块不一致

- AE：按 display value `casefold()` 排序
- Rule-based / Categorical：按 `str(value)` 排序
- SAS codegen 中部分模块又有各自 runtime order

建议建立明确的“Python result treatment ordering”公共 contract，同时允许模块 JSON 声明 resolved/runtime 策略。不要仅为统一代码而改变现有已验收 SAS 输出顺序。

### Cross-4：通用 drill-down builder 的类名过于 Categorical-specific

Rule-based 和 AE 目前都复用 `CategoricalQueryBuilder`。逻辑可复用是好事，但名称会让模块边界混乱。

这是低优先级结构项。等行为测试稳定后，可移动到例如 `analysis_query.py` 并保留兼容 import；不要在统计修复中顺带迁移。

### Cross-5：完整测试仍报告一个未关闭 SQLite connection 的 ResourceWarning

完整 223-test run 全部通过，但在 `test_table_model` 阶段出现一次：

```text
ResourceWarning: unclosed database in <sqlite3.Connection ...>
```

它不属于 Categorical 失败，也不应混入 Categorical 修复 commit。建议后续单独用 `-W error::ResourceWarning` 或 `tracemalloc` 定位是测试 fixture 还是生产连接未关闭，并增加 connection lifecycle regression test。

## 5. 推荐施工顺序

### Phase 1：统计正确性和 Population treatment

1. 补失败回归测试，先锁住当前 bug。
2. 修 distinct subject missing 语义。
3. 修 missing Item level canonicalization/display/drill-down。
4. 让 Item label 正确进入 Wide Result。
5. 确认并实现 0-frequency display contract。
6. 增加 ADSL treatment variable UI/config/engine/drill-down。
7. 运行 Categorical 定向测试、Analysis Controller、UI smoke 和完整 suite。

涉及文件：

```text
clinical_data_viewer/categorical/models.py
clinical_data_viewer/categorical/engine.py
clinical_data_viewer/categorical/result_store.py
clinical_data_viewer/categorical/drilldown.py
clinical_data_viewer/ui/categorical_builder.py
clinical_data_viewer/controllers/analysis/categorical.py
tests/test_categorical_engine.py
tests/test_categorical_builder_state.py
tests/test_analysis_controller.py
tests/test_ui_smoke.py
```

Phase 1 不做 JSON、SAS/R codegen、性能重构。

### Phase 2：Builder Filter ownership 与 Reload 生命周期

1. 移除 Run 时重新询问 source WHERE 的逻辑，Builder Numerator WHERE 成为 authoritative。
2. CategoricalController 接收 Reload started/completed/failed。
3. Builder 增加 `_source_reloading` 状态。
4. metadata refresh 时逐项裁剪失效选择，并返回 removed labels。
5. 保留全部独立 filters 与仍有效配置。
6. 完成后把成熟模式分别迁移到 Listing、Rule-based、AE；每次一个模块、单独回归。

### Phase 3：稳定排序与 Long Result traceability

1. 用户确认 missing treatment policy。
2. 固定 treatment/level ordering contract。
3. 增加 long result 的显式 order 字段。
4. 确认 wide/long/CSV/WHERE/Variables/排序仍是普通 Dataset Tab 能力。

### Phase 4：性能测量与定向优化

1. 准备真实规模 fixture 或脱敏数据。
2. 记录 1、5、20 个 Item 下的运行时间、峰值内存和 SQLite 读取次数。
3. 优先做 denominator cache；只有证据表明不足时再合并聚合查询。
4. 优化前后逐表比较 wide、long、drill-down。

## 6. 测试与回归验收清单

### Engine tests

- Population / Non-missing / n1 三条路径
- distinct subject 与 record count
- `NULL` / `""` subject
- missing Item/Context
- Item label / variable fallback
- 0-frequency treatment cell
- denominator=0
- Total 重新计算，不是 treatment sum
- independent Numerator/Population/Baseline/Postbaseline filters
- source/population treatment 变量名不同
- numeric/character treatment
- context-specific denominator

### Drill-down tests

- Numerator Records
- Numerator Subjects
- Denominator Subjects
- Total 不附加 treatment condition
- Population 使用 ADSL treatment variable 和 Population WHERE
- Non-missing 使用 Numerator WHERE + analysis nonmissing
- n1 使用 Numerator WHERE + baseline/postbaseline
- missing level 命中 NULL 和空字符串
- 0-frequency cell 返回空结果而不是报错

### Builder/Controller tests

- fixed source 不随 Tab 切换
- source 关闭警告
- Clear 才释放 source/配置
- ADSL Browse 和默认 ADSL 识别
- ADSL treatment selector
- Builder empty WHERE 的 authoritative 语义
- invalid WHERE 保留输入并显示错误
- Reload started/completed/failed
- stale Item/Context selection pruning
- busy 状态和 worker failure 恢复
- result close 不清 Builder

### Full regression

每个 Phase 完成后至少执行：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest \
  tests.test_categorical_engine \
  tests.test_categorical_builder_state \
  tests.test_analysis_controller \
  tests.test_ui_smoke

QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

### Windows 手工验收

- 使用真实 `.sas7bdat` 和 `.xpt` 各运行一次。
- Source `TRTA` / ADSL `TRT01A` 计算与 drill-down 一致。
- 空白 Subject 不进入 distinct N。
- 0 cell、denominator=0、missing level 显示正确。
- Reload 后 Builder 输入保留、失效变量被明确移除。
- 大数据运行时 UI 可响应，关闭 Result 后临时目录释放。
- CSV 与当前 wide/long Tab 的列、行、顺序和显示一致。

## 7. 实施前需要确认的业务决策

开始 Phase 1/3 前建议由用户明确确认：

1. 0 numerator 是否统一显示 `0 (0.0)`；推荐是。
2. missing treatment 是阻止计算，还是显示 `(Missing)` treatment column；推荐阻止计算，与 AE/Rule 一致。
3. 纯空格 character subject `"   "` 是否也按 missing；当前项目只统一了 NULL/空字符串。
4. `level_order` 是否近期需要用户配置；若不需要，先标记 reserved，不做 UI。
5. Population denominator 的 context variables 是否必须全部存在于 ADSL；当前实现要求存在且参与 denominator grouping。

## 8. 本轮明确不做

- 不修改 Categorical Python Engine 或 UI。
- 不新增 Categorical JSON。
- 不实现 Categorical SAS/R Generator。
- 不重构 Filter parser。
- 不把计算改成 pandas pipeline。
- 不顺带修改 Rule-based、AE、PROC MEANS 或 Listing 行为。
- 不在业务 contract 未确认前改变 missing treatment 和 zero-cell 显示。

## 9. 完成定义

Categorical 下一轮更新只有同时满足以下条件才可交付：

- Phase 对应的失败测试先建立并在修复后通过。
- wide、long、cell map 和 drill-down 使用同一统计语义。
- source 与 population filters 始终独立。
- distinct subject 的 missing 语义一致。
- 不因 source Reload 清空用户配置或使用陈旧 schema。
- 临时目录 retain/release 没有回归。
- 相关 tests 与完整 223+ test suite 全部通过。
- 明确说明哪些只完成了本地 Python/UI 验证，哪些仍需 Windows/真实临床数据验收。

## 10. 本次施工状态（2026-08-28）

本轮已按 Phase 1 与 Phase 2 的最小范围完成以下更新：

- distinct subject 计数统一排除 character `NULL` / 空字符串 Subject ID；纯空格仍保持项目原有语义，不自动 trim。
- Item/Context 的 character 空值统一 canonicalize 为 missing；启用 missing level 时显示为 `(Missing)`，并与 drill-down 的 `NULL OR ''` 条件一致。
- 已发现 level 会为每个 treatment 写入完整 cell；无 numerator 记录时显示 `0 (0.0)`（分母为 0 时显示 `0 (—)`），同时写入 long result 与 cell map。
- Wide Result 使用用户自定义 Item label，空 label 回退到变量名。
- Population N 增加独立 ADSL treatment variable；source/ADSL 的 treatment、context、distinct subject 类型会校验兼容性，Population drill-down 使用 ADSL treatment variable。
- 缺失 treatment 现在阻止计算，并抛出明确的 `MissingTreatmentError`；source 与 population 两侧均校验。
- Categorical Builder 在首次绑定 source 时继承 source WHERE；后续 Numerator WHERE 由 Builder 独立维护，Run 不再询问是否重新采用 source WHERE。
- Categorical Builder 接入 source Reload started/completed/failed 生命周期；Reload 刷新 metadata、裁剪失效 Item/Context/变量选择，并保留独立 filters。Reload 不会重新覆盖 Builder Numerator WHERE。
- categorical long SQLite 内部增加明确的 `row_order` / `trt_order`，读取时按这两个字段排序，不依赖 rowid。
- `CategoricalItem.level_order` 目前明确作为 reserved contract；若直接提供非空值会报错，避免产生未实现却被静默忽略的配置。

本轮未做：Categorical JSON、SAS/R Generator、性能重构、跨模块 missing helper 抽取。后续应先基于真实临床规模数据 benchmark，再决定 denominator cache 或聚合查询优化。

新增/更新测试覆盖：missing subject、missing Item 合并、零频 cell 与 drill-down、Item label、独立 ADSL treatment 与 drill-down、missing treatment、context 大小写兼容、Builder Reload/配置保留/失效选择裁剪，以及现有 Categorical/UI/Controller 回归。
