# SAS Code Generator 可读性整改计划

状态：待批准，当前仅为施工计划，不包含实现变更。

## 1. 目标

以 Baseline Characteristics SAS Generator 最近完成的可读性优化为参考，对其他 SAS Generator 做同等级别的人工 review 友好化整改。

目标不是重写统计逻辑，而是让生成的 SAS 更接近临床程序员日常维护的代码：

- 数据来源、分析步骤、统计步骤、展示步骤分段清楚；
- 变量名和 WORK 数据集名能看出用途；
- `length`、`set`、筛选、派生和 `label` 的位置稳定；
- 小数位、缺失值、百分比等规则在生成代码中直接可见；
- 代码可直接 review、复制和局部修改；
- 保持 Python Engine、JSON contract、Result、Drilldown 和输出语义不变。

## 2. Baseline 已确立的参考标准

Baseline 当前作为样板，主要采用以下约定：

1. 保留 Jinja2 模板生成方式，Python 只负责准备 context 和校验 contract。
2. 使用来源数据集派生的短 WORK 名称，例如 `work.adsl_source`、`work.adsl_stat_1`。
3. `length` 放在 `set` 前面，并尽量一次声明、一次 `set`。
4. 分析数据、Population N、统计计算、长结果、最终结果和清理分别分段。
5. 已知小数位由 JSON/Jinja 直接生成 SAS `PUT()` 格式，例如 `put(round(mean,0.1),8.1)`。
6. 缺失值替换使用独立语句，不把缺失逻辑和格式逻辑隐藏在复杂表达式中。
7. 分类变量缺失策略互斥：排除时使用 subsetting condition；保留时使用 `if ... then ... else ...`。
8. Label 集中放在最终输出 DATA step，避免散落在多个程序段。
9. 最终输出数据集不进入中间表清理列表。
10. 不使用 `VVALUE()`、无意义的 `_bc_` 等机器化名称；不自动覆盖用户配置的来源名称。

## 3. 全局边界

以下内容在本轮整改中明确不改变：

- Python Engine 的计算公式、分母定义、统计量和排序语义；
- JSON 字段名称、配置版本和 Result/Drilldown contract；
- Builder UI、MainWindow、AnalysisController 业务流程；
- SAS 输出数据集名称和用户可见列名；
- XPT 的既有 `LIBNAME XPORT` 读取路径；
- 现有模块对 source / population dataset 的选择语义；
- 过滤条件必须继续从 AST 渲染，不能直接拼接未经校验的文本；
- 不为了“看起来统一”而合并不同模块的统计逻辑。

特别注意：Baseline 目前只允许 SAS7BDAT，因此其 LIBNAME 处理不能直接复制到仍支持 XPT 的模块。每个模块必须先确认输入格式 contract，再决定是否保留或移除 LIBNAME。

## 4. 模块整改建议

### 4.1 PROC MEANS Builder（经典横向）

涉及：

- `clinical_data_viewer/codegen/sas/generator.py`
- `clinical_data_viewer/codegen/sas/templates/proc_means.sas.j2`

建议：

1. 保持现有 PROC MEANS 统计语义，只整理代码分段和命名。
2. 将 source preparation、BY sort、statistics、subject N、decimal runtime、cleanup 明确分段。
3. `work_source`、统计表成员名和 long result 名称改为来源数据集派生的可读名称，同时保证不同分析变量不会冲突。
4. `length` 放到 `set` 前；同一个 DATA step 不重复 `set`。
5. 已由 Builder 固定的小数位直接生成 `PUT()`；只有确实需要运行时推断时才保留运行时 decimal 逻辑。
6. `select ... into`、`from ... where ...` 的简单语句保持单行，避免把一条简单 SQL 拆成机器式多行。
7. 保留用户选择的 BY、CLASS、Analysis Variable、SUBJECT_N 和 decimal group semantics。
8. 清理区只删除中间 WORK 表，不删除最终结果。

验收重点：多分析变量、无分组、BY/CLASS、subject N、decimal group、filter 和最终结果名称完全不变。

### 4.2 PROC MEANS Table Builder（Vertical）

涉及：

- `clinical_data_viewer/codegen/sas/proc_means_vertical_generator.py`
- `proc_means_vertical.sas.j2`
- `proc_means_vertical_rows.sas.j2`

建议：

1. 保留 Population N 的 `N1/N2/NT` 规则和当前 group-as-row 语义。
2. `source`、`population`、`group levels`、`population N`、`statistics`、`display rows`、`final output` 分段。
3. 检查并减少重复的 `%let n1=&n1;` 类包装；只有为 SAS 宏解析或 header label 明确需要时才保留，并在注释中说明原因。
4. 将 `grp0`、`grp`、`dec_*`、`stat_*` 等名称逐步改成可读的来源派生名称；不能改变最终 output contract。
5. Group label、item label、statistic label 集中输出，避免在每个统计量分支重复维护。
6. 已固定的 decimal 直接生成固定 `PUT()`；`AUTO` 仍需运行时推断时，保留清晰的 decimal calculation block，不把它伪装成静态格式。
7. Population missing treatment 的错误/警告语义保持不变。

验收重点：Baseline 行、Week/Visit 行、多个 Analysis Variables、不同 base decimal、CI、Q1/Q3、Min/Max、Total 和 drilldown 数据不变。

### 4.3 Categorical Table

涉及：

- `clinical_data_viewer/codegen/sas/categorical_generator.py`
- `categorical_table.sas.j2`

建议：

1. 保留 XPT 和 SAS7BDAT 的现有输入路径；不直接套用 Baseline 的 SAS7BDAT-only 限制。
2. 对 SAS7BDAT/XPT source、population source 的准备步骤分别加清晰注释，避免多个 LIBNAME 段看起来像重复业务逻辑。
3. 将 `cat_src`、`pop_src`、`calculation`、`rows` 等中间表名称按 source/item 派生，或至少在注释中明确用途；不得改变 JSON 中用户指定的最终输出名称。
4. 将 `if missing(...) then delete;` 改为等价的 subsetting condition 或显式保留条件，但必须逐个确认 missing policy，不能机械替换。
5. item label 继续集中在单一 `PROC FORMAT`/label block，不能重新散落到每个 item 的 DATA step。
6. `col1-colN` 是现有输出 contract，第一阶段不改列名；只优化内部变量名和注释。
7. percentage rounding、missing level、Total、frequency descending、custom order、propcase 的代码保持直接可读。

验收重点：多个 item、多个 context、population denominator、baseline/postbaseline denominator、missing level、Total、四种 level sort 和 propcase。

### 4.4 Rule-based Table

涉及：

- `clinical_data_viewer/codegen/sas/rule_based_generator.py`
- `rule_based.sas.j2`

建议：

1. source filter 从：

   ```sas
   if not (condition) then delete;
   ```

   改为更接近人工程序的：

   ```sas
   if condition;
   ```

   但只针对明确的 source/filter 语义；不能影响 missing policy。
2. 每个 rule row 的 source、denominator、count、percentage 和 final display 明确分段。
3. 继续保留 `item='Event'` 以及用户定义的 item 文本；集中 label 不得被模板循环重新分散。
4. `data _null_` validation 可以保留，但错误信息应包含 row order、item 和来源，避免只显示机器化 member 名。
5. `work_source`、`work_population`、row member 等内部名称改为来源和 row 顺序组合的可读名称。
6. `cnt{i}`、`den{i}`、`col{i}` 的数组逻辑可保留，因为它表达列数组；补充注释说明 `cnt` 是 numerator、`den` 是 denominator。
7. `n (%)` 的格式保持直接 `PUT()`，division by zero 继续输出约定的 `0 (—)`。
8. XPT LIBNAME 必须保留并在 cleanup 中正确 clear。

验收重点：source filter、row filter、same-universe denominator、population denominator、Total、0 denominator、numeric treatment、XPT 和 item label。

### 4.5 Change from Baseline

涉及：

- `change_from_baseline_generator.py`
- `change_from_baseline.sas.j2`

建议：

1. 保留当前 horizontal shell、固定 treatment order、Total、SUBJECT_N 和 statistics contract。
2. `cb_source`、`cb_stat`、`cb_subject`、`cb_group` 等名称可保留第一阶段，但在每段注释中明确其用途；第二阶段再考虑按 source member 派生。
3. 将 `Prepare source`、`PROC MEANS`、subject N、Total recalculation、long result、wide result、cleanup 分段统一。
4. 缺失 subject、missing treatment、missing baseline/visit 的处理必须和 Python Engine 一致；不能为了代码简短而把 warning 改成 delete。
5. item label、statistic label、treatment label 集中生成，避免在多个 `PROC SQL` 和 DATA step 中重复。
6. `VALUE`、`ITEM` 等最终列名属于当前输出 contract，第一阶段不直接改；内部中间值可以采用 `row_value` 风格。
7. Population N 的 `%let` 仅在宏变量会被 header label 使用时保留，并加注释说明来源。

验收重点：多 item、Visit/Visit+Treatment、Total、distinct subject N、statistic decimals、missing behavior、drilldown contract 和 generated output columns。

### 4.6 AE SOC/PT Table

涉及：

- `ae_table_generator.py`
- `ae_table.sas.j2`

建议：

1. 保留动态 treatment level、独立 population denominator、SOC/PT missing policy 和 Total semantics。
2. `_trt`、`_soc`、`_pt`、`_subjid` 等内部变量逐步改成更清楚的短名称，例如 `treatment_key`、`soc_key`、`pt_key`；如果长度过大，则至少在 section comment 中声明含义。
3. source preparation、population preparation、treatment levels、denominator、Any AE、SOC、PT、sorting、final report 分段保持固定顺序。
4. `if missing(subject) then delete` 只有在 contract 明确排除 subject missing 时才保留；否则改为 warning/validation，不能默默丢记录。
5. treatment missing 的错误处理继续保留，但统一错误文案和来源说明。
6. 动态 treatment column label 继续使用运行时逻辑；不要为了可读性回退成静态 treatment levels。
7. final `display_value` 可评估改为 `row_value`，但必须同步 long result、cleanup 和测试，第一阶段不改变最终输出列。

验收重点：SOC/PT uncoded、missing policy 独立生效、动态 treatment、population treatment variable、Total、XPT、numeric treatment 和 runtime sorting。

### 4.7 Listing Generator

涉及：

- `listing_generator.py`
- `listing.sas.j2`

建议：

1. Listing 已经有较好的分层结构，优先做小范围可读性修整，不重新设计 Listing contract。
2. 保留 source left merge ADSL、filter、DATA step、SORT、PROC REPORT 的四层顺序。
3. `lst_main`、`lst_adsl`、`listing_base`、`listing_prep`、`listing_sorted` 的命名应统一，并在注释中说明“source / merged / prepared / sorted / final”。
4. `length` 放在相应 `set` 前；derived character length 继续显式声明，避免截断。
5. expression、numeric-to-character、PUT/INPUT、division-by-zero、CATX missing 的代码保持一条表达式一条责任，必要时增加注释，不把复杂表达式塞进 PROC REPORT。
6. hidden sort variable 仍进入 PROC REPORT，并使用 `ORDER`；不能为了隐藏而删除排序语义。
7. final dataset 不得被 cleanup 删除；增加最终输出保留测试。

验收重点：ADSL merge、visible/hidden sort columns、long character width、numeric concat、PUT/INPUT、CATX missing、division by zero、PROC SORT 和 PROC REPORT column order。

### 4.8 Shift Table

涉及：

- `shift_table_generator.py`
- `shift_table.sas.j2`

建议：

1. 保留多 PARAMCD、Visit 纵向矩阵、Treatment 横向列、可选 Total、Population N 和 drilldown 语义。
2. `parm_ord`、`visit_ord`、`trt_ord`、`grade_ord` 等顺序字段可以保留，但在注释中明确它们是“展示顺序”，不能与原始变量混淆。
3. `raw`、`src`、`detail`、`row_total`、`column_total`、`grand_total` 分段命名统一；避免同一含义在不同 section 中使用不同缩写。
4. repeated `PROC SQL` blocks 保持业务顺序，但将 select/from/group by 的结构格式化为人类可 review 的布局。
5. Population N 的 `%let n1/%let nt` 只在实际 header 或后续表达式使用时保留；若保留，注释明确来源和用途。
6. `display_value` 可评估统一改为 `row_value`，但先不改最终结果 contract。
7. detail、row total、column total、grand total 的计算逻辑不能合并成难以 review 的宏循环。

验收重点：多个 PARAMCD、Visit 顺序、Treatment 横向顺序、Total、grade matrix、subject N、missing/warning、drilldown 和 Gallery 现有入口。

## 5. 推荐施工顺序

每个模块单独一个 commit，完成后先跑模块测试，再继续下一个模块：

1. Rule-based：先统一 `if condition;`、label、percentage 和中间表命名。
2. Categorical：处理 missing branch、source/population section、central labels。
3. PROC MEANS Vertical：处理 N1/N2/NT、group labels、decimal display。
4. Change from Baseline：整理 source/stat/subject/total/long/wide sections。
5. AE Table：整理层级、missing policy 和动态 treatment sections。
6. Shift Table：整理多矩阵 SQL 和中间表命名。
7. Listing：只做低风险命名和 section polish，不碰已稳定的 merge/report contract。
8. 经典 PROC MEANS：最后整理，因为它与现有 decimal runtime contract 关联较多。

不建议第一步抽出一个“大一统 SAS style renderer”。先完成两个模块后，再评估是否只抽取安全的共享 Jinja filters，例如：

- SAS name/string literal；
- round increment；
- readable WORK prefix；
- label literal。

共享 helper 只能处理渲染格式，不能承载模块业务判断。

## 6. 每个模块的实施步骤

1. 先读取当前 generator、template、configuration/model、相关 tests。
2. 列出当前生成代码中的 public output、intermediate table、macro、cleanup 对照表。
3. 只调整 Jinja context helper 和模板结构，不修改 Python Engine。
4. 将所有命名变化限制在 SAS 内部；如果会影响 output 或 drilldown，必须停止并单独确认。
5. 增加可读性 contract tests：名称、section 顺序、`length`/`set` 顺序、missing branch、label、format、cleanup。
6. 运行该模块全部测试和所有相关 codegen regression tests。
7. 运行全量测试、Ruff、`git diff --check`。
8. 生成一份真实配置的 SAS sample，人工 review 后再提交。

## 7. 测试与回归验收

### 7.1 通用 codegen tests

每个模块至少增加或确认以下测试：

- 仍使用 Jinja template；
- source/filter 从 AST 正确渲染；
- JSON/Python 配置 contract 不变；
- `length` 在 `set` 前；
- 不重复 `set` 同一 source；
- missing policy 与 Python 语义一致；
- 已知小数位使用直接 `PUT()`；
- missing display 使用独立语句；
- label 集中且最终输出列正确；
- 中间表清理不删除最终结果；
- treatment、Total、Population N 顺序保持；
- XPT contract 未被误改；
- 生成代码不出现未解析的 Jinja 标记。

### 7.2 模块回归组合

每次单模块更新至少运行：

```text
该模块的 test_*_sas_codegen.py
相关 Builder / Engine / Result / Drilldown tests
其他 SAS codegen regression tests
完整 tests suite
Ruff
git diff --check
```

### 7.3 可选真实 SAS 验证

如果测试环境提供 SAS runtime，再增加：

- generated SAS syntax check；
- source dataset 和 population dataset 的真实运行；
- missing、zero denominator、duplicate subject、empty treatment level；
- 输出列、label、统计值与 Python reference result 对照。

如果没有 SAS runtime，不得声称“已运行 SAS”；只能报告模板 contract tests 和 Python regression 结果。

## 8. 风险和停止条件

遇到以下情况应停止当前模块并先确认：

- 需要修改 JSON version 或公开配置字段；
- 需要修改最终 output dataset/column 名称；
- 需要改变 XPT 支持方式；
- 需要把 warning 改成 error 或把 error 改成 silent delete；
- Python 与 SAS 的 missing、denominator、rounding 语义不一致；
- 一个变量名同时被 Python result、SAS output 和 drilldown 使用；
- 为了可读性需要把两个独立统计过程合并成一个复杂宏循环。

## 9. 批准后的交付格式

每个模块完成后报告：

1. 修改的 generator/template 文件；
2. 保持不变的 public contract；
3. 生成代码可读性变化；
4. 新增/调整测试；
5. 目标测试结果；
6. 全量测试结果；
7. 是否实际运行 SAS；
8. 未处理的边界和下一模块计划。
