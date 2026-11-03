# Shift Table Builder 设计与施工方案

本文档依据已确认的 Shift from Baseline table shell 需求，以及
`docs/table-shell-builder-codegen-gallery-execution-playbook.md` 编写。它是后续施工、测试和
验收的业务 contract；实现不得在施工过程中自行改变本文件已经冻结的统计语义。

## 1. 当前状态与施工边界

- 当前阶段只完成设计与施工规划，尚未开始实现。
- 新功能作为独立分析模块增加，不修改现有 PROC MEANS、PROC MEANS Vertical、
  Categorical、Rule-based、AE Table、Baseline Characteristics、Change from Baseline、
  Listing 或 Merge 的业务逻辑。
- `MainWindow` 只增加菜单、Builder 路由和 Analysis Dock 显示等窗口级接线，不得包含
  Shift Table 的筛选、计数、分母、Total、排序、JSON、drilldown 或 SAS 生成逻辑。
- Python reference engine 是业务语义的第一份实现；SAS Code Generator 必须消费同一份
  JSON contract，并使用 Jinja2 模板渲染。
- 第一版只实现 Treatment 横向展开，不实现 Treatment Down Rows。

## 2. 模块身份

| 字段 | 约定 |
| --- | --- |
| Module key | `shift_table` |
| Tools 菜单名称 | `Shift Table Builder` |
| Builder 标题 | `Shift Table Builder` |
| Result kind | `shift_table` |
| Long result kind | `shift_table_long` |
| Configuration file | `shift_table_config.json` |
| Python reference engine | `python_shift_table_v1` |
| SAS final output | `work.shift_table` |
| SAS long output | `work.shift_table_long` |
| Gallery card title | `Shift from Baseline` |
| Gallery category/tag | `Safety`、`Categorical` |

## 3. Table shell 的业务含义

Shift Table 比较同一受试者在 Baseline 和某个 post-baseline Visit 的等级：

```text
Baseline grade
        ↓
Post-baseline grade at a selected Visit
```

例如：

```text
USUBJID          001
PARAMCD          ALT
AVISIT           Week 4
TRTA             Drug A
Baseline grade   1
Post grade       2
```

该受试者进入：

```text
Parameter = ALT
Visit = Week 4
Treatment = Drug A
Baseline row = 1
Post-baseline column = 2
```

对应的 `n (%)` cell。

第一版不实现原参考图灰色区域中的：

- `Discordant (excluding missing) n (%)`；
- `Kappa statistics (95% CI)`。

第一版也不：

- 判断 improvement / worsening；
- 推导 maximum post-baseline grade；
- 计算 Kappa；
- 生成 footnote；
- 生成 Missing grade 行或列；
- 支持 Treatment Down Rows。

## 4. Source 与变量 contract

### 4.1 Analysis Source

Builder 从当前已经打开的数据集中选择一个 Analysis Source：

- source 不绑定打开 Builder 时的当前 Dataset Tab；
- source 列表只包含当前已打开且可分析的数据集；
- 未打开数据集时显示英文提示，要求先打开数据集；
- source 被关闭时保留 Builder 输入，但 Run 和 Code Generator 必须阻止并提示重新选择；
- 除非用户点击 `Clear`，不得清空已有输入；
- Builder Filter 独立于 Dataset Tab 后续 WHERE 修改。

第一版要求用户选择以下分析变量：

| 角色 | 示例 | 说明 |
| --- | --- | --- |
| Subject Variable | `USUBJID` | Cell、分母和 drilldown 的受试者标识 |
| Parameter Variable | `PARAMCD` | 用户从 raw levels 中选择一个或多个 Parameter |
| Visit Variable | `AVISIT` | Visit 始终纵向展开 |
| Treatment Variable | `TRTA` | Treatment 第一版只横向展开 |
| Baseline Grade Variable | `BTOXGR` | 数据集中已经存在的 Baseline grade |
| Post-baseline Grade Variable | `ATOXGR` | 数据集中已经存在的 post-baseline grade |
| Analysis Flag Variable | `ANL01FL` | 选择适合当前 Shift 分析的记录 |
| Analysis Flag Value | `Y` | 与 Flag Variable 组成分析条件 |

Baseline grade 和 post-baseline grade 必须直接读取现有变量。Engine 不根据变量名猜测语义，
也不自动选择最大值、第一条、最后一条或平均值。

### 4.2 Dataset Filter 与 Analysis Flag

Builder 提供独立的 SAS-like Dataset Filter，继续复用现有 `FilterEngine`、Filter AST 和
跨语言 renderer，不新增 WHERE parser。

记录范围的固定顺序是：

```text
Analysis Source
    → Dataset Filter
    → Analysis Flag condition
    → selected Parameter levels
    → selected Visit levels
    → selected Treatment levels
    → Baseline/Post grade completeness
```

Analysis Flag 只表示哪些 source records 适合分析。它不等价于 maximum grade flag，也不得
触发额外推导。

### 4.3 Population Source

Header Population N 使用独立的 Population Dataset，通常为 ADSL。Builder 提供：

- Population Dataset；
- Population Subject Variable；
- Population Treatment Variable；
- Population WHERE。

Analysis 和 Population 的 Treatment 变量名可以不同，例如：

```text
Analysis Source:   TRTA
Population Source: TRT01A
```

但已选择 treatment levels 的 raw values 必须能明确对应。第一版不根据相似文本自动匹配
不同的 treatment values；发现不一致时必须提示用户修改选择或 Filter。

Population Dataset 未选择时，是否允许 fallback 到 Analysis Source，应沿用现有
PROC MEANS Vertical 的交互：明确 warning，用户确认后才使用 Analysis Source 计算 Header N。

## 5. Parameter、Visit、Treatment 与 Grade 顺序

### 5.1 Parameter

第一版允许选择多个 Parameter level。每个 Parameter 保存：

- raw value；
- display label；
- explicit order。

Parameter 按用户在 Builder 中的顺序纵向输出，不按字符、format 或数据库返回顺序重新排序。
Builder 提供 `Add`、`Remove`、`Move Up`、`Move Down`。

Parameter label 集中维护，例如：

```text
ALT = Alanine Aminotransferase
AST = Aspartate Aminotransferase
```

Python display、JSON 和 SAS template 只能消费这一份集中 label 配置，不得把 Parameter label
散落在统计分支或模板各处。

### 5.2 Visit

Visit 始终通过增加行/section 纵向展示，不增加横向 Visit columns。

第一版维护一份用户选择的 Visit raw value、display label 和顺序列表。该顺序在每个
Parameter 内部重复使用。若某个 Parameter 在某个选中 Visit 没有分析记录，则不创建空矩阵；
Long Result 仍保留完整 order contract，便于审计为什么该 section 未生成。

最终层级固定为：

```text
Parameter 1
    Visit 1
        Shift matrix
    Visit 2
        Shift matrix

Parameter 2
    Visit 1
        Shift matrix
    Visit 2
        Shift matrix
```

### 5.3 Treatment

第一版 Treatment 只横向展开，每个 treatment 是一个 column block。Builder 维护选中的
treatment raw value、display label 和顺序，不依赖 SQLite 的默认 level 顺序。

用户可以选择：

```text
[ ] Include Overall Total
```

- 未勾选时，只显示各 Treatment block；
- 勾选时，在最右侧增加 `Total` treatment block；
- 第一版不提供 Treatment Layout 选择器，也不保存未实现的 `down_rows` 配置。

### 5.4 Grade levels

Baseline 行和 post-baseline 列共用一份用户配置的 ordered grade levels。每个 level 保存：

- raw typed value；
- display label；
- explicit order。

等级不得写死为 `0, 1, 2, 3, 4`。以下配置同样合法：

```text
0 = Normal
1 = Mild
2 = Moderate
3 = Severe
```

或字符等级：

```text
LOW
NORMAL
HIGH
```

Grouping、sorting、count、JSON、SAS 和 drilldown 使用 raw typed values；display label 只用于
展示。不得根据 formatted display value 分组。

运行前扫描已过滤数据中的非 missing grade。若发现未配置 raw level，必须阻止 Run 并列出
缺失配置的值；不得静默排除或自动追加到最后。

## 6. Count、分母与 Population N

### 6.1 普通 Cell 的 n

每个 cell 的 numerator 是以下组合下的 distinct `USUBJID`：

```text
Parameter
× Visit
× Treatment
× Baseline grade
× Post-baseline grade
```

公式：

```text
cell_n = count(distinct USUBJID)
```

它不是 record count。

### 6.2 百分比分母

每个 Treatment block 的百分比分母是当前：

```text
Parameter × Visit × Treatment
```

下同时满足以下条件的 distinct `USUBJID`：

- 通过 Dataset Filter；
- 通过 Analysis Flag；
- Baseline grade 非 missing；
- Post-baseline grade 非 missing；
- 两个 grade raw values 均在用户配置的 grade levels 中。

公式：

```text
cell_pct = cell_n / analyzable_subject_n × 100
```

它是整个 Shift matrix 的受试者分母，不是 row percentage 或 column percentage。

### 6.3 Header Population N

Treatment header 中的：

```text
Drug A (N=xxx)
Drug B (N=xxx)
Total  (N=xxx)
```

来自 Population Dataset，在 Population WHERE 后计算：

```text
count(distinct Population Subject Variable)
```

- 每个 Treatment N 独立计算；
- Total N 跨选中的非 missing Treatment levels 重新计算 distinct subjects；
- Total N 不允许通过 treatment Ns 相加；
- Header N 不随 Parameter 或 Visit 改变；
- Analysis Dataset Filter 不得错误应用到独立 Population Dataset。

### 6.4 两种 N 必须分离

| 数值 | 来源 | 用途 |
| --- | --- | --- |
| Header Population `N` | Population Dataset | 描述治疗组人群规模 |
| Matrix analyzable subject `n` | Analysis Source | `n (%)` 的百分比分母 |

例如：

```text
Drug A header N                    128
ALT / Week 4 analyzable denominator 121
ALT / Week 8 analyzable denominator 117
```

这是允许且预期的结果。

## 7. Total contract

### 7.1 Matrix Total row、Total column 和 grand total

每个 Treatment block 固定包含：

- 每个 Baseline grade 的 row Total；
- 每个 Post-baseline grade 的 column Total；
- Grand Total cell。

它们全部独立重新计算 distinct `USUBJID`：

```text
Row Total
  = 当前 Parameter/Visit/Treatment/Baseline grade 下，
    所有有效 Post grades 的 distinct USUBJID

Column Total
  = 当前 Parameter/Visit/Treatment/Post grade 下，
    所有有效 Baseline grades 的 distinct USUBJID

Grand Total
  = 当前 Parameter/Visit/Treatment 下，
    Baseline/Post grade 都有效的 distinct USUBJID
```

不能通过普通 cells 相加得到 Total。

### 7.2 Overall Total treatment block

勾选 `Include Overall Total` 时，在最右侧增加 Overall Total block。该 block：

- 不增加 Treatment 条件；
- 只包含用户选中的非 missing Treatment levels；
- cell、row total、column total、grand total 均跨 Treatment 独立计算 distinct `USUBJID`；
- 不允许把各 Treatment block 的结果相加。

## 8. Missing、重复记录与异常数据

### 8.1 Missing grade

第一版不生成 Missing grade 行或列：

- Baseline grade missing 的记录不进入矩阵；
- Post-baseline grade missing 的记录不进入矩阵；
- 这些记录也不进入 analyzable percentage denominator；
- Run 前或完成摘要中显示因 Baseline/Post grade missing 而排除的 record 和 distinct subject 数；
- 不在用户不知情的情况下静默排除。

未来可以增加 `Include Missing Grade`，但不得在 v1 中加入未测试的隐藏开关。

### 8.2 Missing subject、Parameter、Visit 或 Treatment

- Missing `USUBJID` 不进入任何 subject count，并在运行前明确 warning；
- Missing Parameter 不进入已选择 Parameter sections；
- Missing Visit 不进入已选择 Visit sections；
- Missing Treatment 不进入 Treatment block 或 Overall Total，运行前提示用户通过 Filter 处理；
- 不把 missing treatment 自动显示为 `Uncoded`。

### 8.3 重复分析记录

运行前检查：

```text
USUBJID × Parameter × Visit × Treatment
```

如果同一组合存在多条通过 Filter 和 Analysis Flag 的记录，显示：

```text
Multiple analysis records were found for the same subject,
parameter, visit, and treatment group.

A subject may contribute to more than one shift cell.
Please review the analysis flag and filter before continuing.
```

按钮：

```text
[Go Back] [Continue]
```

选择 Continue 后：

- Engine 不自动取最大值、第一条、最后一条或平均值；
- 每个 cell 仍按 distinct `USUBJID` 计算；
- Total 仍独立重新计算 distinct `USUBJID`；
- 同一受试者可能贡献多个 cells；
- 百分比合计可能超过 100%；
- 完整重复 source records 必须保留在 drilldown 中。

## 9. Display contract

### 9.1 Wide result

Viewer 默认显示临床三线表风格的 Wide Result。每个 Parameter/Visit section 结构如下：

```text
Parameter: Alanine Aminotransferase
Visit: Week 4

                 Drug A (N=128)                 Drug B (N=127)                 Total (N=255)
Baseline     Grade 0 Grade 1 Grade 2 Total   Grade 0 Grade 1 Grade 2 Total   Grade 0 ... Total
Grade 0       n (%)   n (%)   n (%)   n (%)    n (%)   n (%)   n (%)   n (%)    n (%) ... n (%)
Grade 1       n (%)   n (%)   n (%)   n (%)    n (%)   n (%)   n (%)   n (%)    n (%) ... n (%)
Grade 2       n (%)   n (%)   n (%)   n (%)    n (%)   n (%)   n (%)   n (%)    n (%) ... n (%)
Total         n (%)   n (%)   n (%) n (100%)   n (%)   n (%)   n (%) n (100%)  n (%) ... n (100%)
```

Total treatment block 仅在启用时出现。

### 9.2 Display value

- 普通值显示为 `n (xx.x)`；
- percent digits 默认 `1`，允许 `0–4`；
- Percentage rounding 统一使用 `ROUND_HALF_UP`；
- `n` 始终为整数；
- zero denominator 显示 `0 (-)`；
- grand total 在无重复分析记录时通常显示 `n (100.0)`；
- 不把 percent 符号写入 numeric raw result；display string 单独保存。

### 9.3 Long result

同时保存可审阅的 Long Result，至少包含：

```text
parameter_order
parameter_raw
parameter_label
visit_order
visit_raw
visit_label
treatment_order
treatment_raw
treatment_label
baseline_grade_order
baseline_grade_raw
baseline_grade_label
post_grade_order
post_grade_raw
post_grade_label
cell_role
subject_n
denominator_n
percent_value
display_value
```

`cell_role` 至少区分：

```text
detail
row_total
column_total
grand_total
```

Wide Result 的排序必须来自显式 order fields，不依赖 SQLite 插入顺序。

## 10. Drilldown contract

Drilldown 是 v1 必需功能。双击结果 cell 和右键
`Drill Down to Source Rows` 必须进入同一条 signal/controller 路径。

### 10.1 Analysis result cells

| Cell 类型 | Drilldown universe |
| --- | --- |
| Detail | 当前 Parameter、Visit、Treatment、Baseline grade、Post grade |
| Row Total | 当前 Parameter、Visit、Treatment、Baseline grade，所有有效 Post grades |
| Column Total | 当前 Parameter、Visit、Treatment、Post grade，所有有效 Baseline grades |
| Grand Total | 当前 Parameter、Visit、Treatment，Baseline/Post grade 都有效 |
| Overall detail/total | 相同条件，但不附加单一 Treatment 条件，只限选中的 Treatment levels |

重复记录必须原样显示，不能因为结果按 distinct subject 计数而在 Query Result 中静默去重。

### 10.2 Header Population N

- Treatment Header N drilldown 到 Population Dataset 中该 treatment 的 source rows；
- Overall Header N drilldown 到 Population Dataset 中选中非 missing treatment levels 的 rows；
- Query Result 中允许保留同一 subject 的多条 population rows，但显示的 N 仍是 distinct subject；
- Population drilldown 使用 Population WHERE，不使用 Analysis Dataset Filter。

### 10.3 Cell map

`result_store.py` 保存 raw typed cell mapping，禁止从显示文本反向解析：

```json
{
  "parameter": "ALT",
  "visit": "Week 4",
  "treatment": "Drug A",
  "baseline_grade": 1,
  "post_grade": 2,
  "cell_role": "detail",
  "is_overall": false
}
```

结果经 WHERE、排序、分页后，必须通过 `_source_row` 和 cell map 定位。source/result 已关闭、
context 丢失或 cell 无映射时显示明确错误，不得查询其他当前 tab。

## 11. Builder UI 设计

Builder 主体放入同时支持横向和纵向滚动的区域，并使用主界面的浅蓝白样式。
ComboBox、SpinBox 采用 click-only 行为，鼠标滚轮不得改变选择。

建议区域：

```text
Source
  Analysis Dataset
  Dataset Filter

Variables
  Subject Variable
  Parameter Variable
  Visit Variable
  Treatment Variable
  Baseline Grade Variable
  Post-baseline Grade Variable
  Analysis Flag Variable / Value

Parameters
  Raw Value | Display Label | Up | Down | Remove

Visits
  Raw Value | Display Label | Up | Down | Remove

Treatments
  Raw Value | Display Label | Up | Down | Remove
  [ ] Include Overall Total

Grade Levels
  Raw Value | Display Label | Up | Down | Remove

Population N
  Population Dataset
  Population Subject Variable
  Population Treatment Variable
  Population WHERE

Display
  Percentage Decimal Places [0–4]

Actions
  Shell Preview | Clear
  SAS Code       | Run
```

要求：

- Add/Remove/Up/Down 按钮在窄 Dock 下仍然可见并有可识别文本或 tooltip；
- Parameter、Visit、Treatment、Grade Level 的 UI 顺序就是最终输出顺序；
- source 或 variable 改变时，不自动清空用户已维护的 labels/order；若已有 raw value 不再有效，
  将该行标记为 invalid 并阻止 Run；
- busy 时禁用 Run、Clear、source 切换和 Code Generator；
- Code Generator 只有在 config validation 通过且 source kind 支持时 enabled；
- Run 使用后台 worker，不阻塞 Viewer。

## 12. Domain models 与 validation

建议模型：

```python
@dataclass(frozen=True, slots=True)
class OrderedLevel:
    raw_value: object
    display_label: str
    order: int


@dataclass(frozen=True, slots=True)
class ShiftTableConfig:
    subject_variable: str
    parameter_variable: str
    visit_variable: str
    treatment_variable: str
    baseline_grade_variable: str
    post_grade_variable: str
    analysis_flag_variable: str
    analysis_flag_value: object
    parameters: tuple[OrderedLevel, ...]
    visits: tuple[OrderedLevel, ...]
    treatments: tuple[OrderedLevel, ...]
    grade_levels: tuple[OrderedLevel, ...]
    compiled_filter: CompiledFilter
    filter_text: str
    include_overall_total: bool
    percent_digits: int
    population: PopulationConfig
```

运行前至少验证：

1. Analysis Source 和 Population Source 仍然打开且 cache complete；
2. 所有 referenced variables 存在；
3. Analysis Flag value 与变量类型兼容；
4. Parameter、Visit、Treatment、Grade Level 均至少选择一个；
5. 每类 ordered levels 的 raw values 唯一、order 唯一；
6. display label 非空；
7. Baseline/Post grade storage type 与 configured raw values 兼容；
8. Filter 可由现有 `FilterEngine` 编译；
9. 分析与 Population Subject/Treatment 类型和 raw levels 可明确对应；
10. percent digits 在 `0–4`；
11. filtered data 中不存在未配置的非 missing grade；
12. 重复分析记录已由用户确认或返回修改；
13. source kind 符合 Python/SAS codegen contract；
14. Merge source 如不支持 SAS codegen，SAS 按钮保持 disabled。

Validation error 必须指出具体字段和值，不允许 silent fallback。

## 13. Python Reference Engine

### 13.1 固定计算步骤

```text
1. Validate config and source metadata
2. Apply Dataset Filter and Analysis Flag
3. Restrict to selected Parameter / Visit / Treatment levels
4. Scan missing grades, unconfigured grades and duplicate analysis keys
5. Resolve Header Population N
6. Resolve analyzable denominators by Parameter × Visit × Treatment
7. Count distinct subjects for detail cells
8. Independently calculate row/column/grand totals
9. Independently calculate Overall Total when enabled
10. Build ordered long result
11. Pivot to wide Viewer result
12. Save cell map and configuration JSON
```

实现应优先使用 SQLite `GROUP BY` 与 `COUNT(DISTINCT ...)`，避免把整个 source 加载到 Python
list 或 pandas DataFrame。相同 denominator、population N 和 ordered runtime values 不重复扫描。

### 13.2 Raw value 原则

- SQLite 继续保存 raw values；
- Filter、grouping、sorting、count 和 drilldown 使用 raw values；
- VariableMetadata format 只影响 display；
- 不把 numeric grade 永久转成 character label；
- JSON 中同时保存 raw typed value 和 display label。

## 14. Result Store

建议临时 SQLite tables：

```text
shift_table_result
shift_table_long
shift_table_cell_map
shift_table_header_map
```

- `shift_table_result`：Viewer wide result；
- `shift_table_long`：审阅、导出和 codegen parity fixture；
- `shift_table_cell_map`：analysis cell 到 raw source universe 的映射；
- `shift_table_header_map`：Population Header N 到 population universe 的映射。

临时目录：

```text
TempManager session directory/
└── <result-id>/
    ├── dataset.sqlite
    └── shift_table_config.json
```

不得写回 source dataset，也不得在模块中硬编码 Windows/macOS 临时路径。

## 15. Configuration JSON v1

建议结构：

```json
{
  "type": "shift_table",
  "version": 1,
  "input": {
    "source_kind": "sas7bdat",
    "source_name": "ADLB",
    "source_member": "adam.adlb"
  },
  "dataset_filter": {
    "language": "sas_like",
    "text": "SAFFL = 'Y'",
    "ast": {}
  },
  "variables": {
    "subject": "USUBJID",
    "parameter": "PARAMCD",
    "visit": "AVISIT",
    "treatment": "TRTA",
    "baseline_grade": "BTOXGR",
    "post_grade": "ATOXGR",
    "analysis_flag": "ANL01FL",
    "analysis_flag_value": "Y"
  },
  "ordering": {
    "parameters": [
      {"raw": "ALT", "label": "Alanine Aminotransferase", "order": 1}
    ],
    "visits": [
      {"raw": "Week 4", "label": "Week 4", "order": 1}
    ],
    "treatments": [
      {"raw": "Drug A", "label": "Drug A", "order": 1}
    ],
    "grade_levels": [
      {"raw": 0, "label": "0", "order": 1},
      {"raw": 1, "label": "1", "order": 2}
    ]
  },
  "population": {
    "input": {
      "source_kind": "sas7bdat",
      "source_name": "ADSL",
      "source_member": "adam.adsl"
    },
    "subject_variable": "USUBJID",
    "treatment_variable": "TRT01A",
    "filter": {
      "language": "sas_like",
      "text": "SAFFL = 'Y'",
      "ast": {}
    }
  },
  "calculation": {
    "count": "distinct_subject",
    "denominator": "parameter_visit_treatment_complete_grade_subjects",
    "duplicate_policy": "warn_and_continue",
    "missing_grade_policy": "exclude_with_summary",
    "total_policy": "independent_distinct_subject",
    "include_overall_total": true,
    "reference_engine": "python_shift_table_v1"
  },
  "display": {
    "treatment_layout": "across_columns",
    "visit_layout": "vertical_sections",
    "parameter_layout": "vertical_sections",
    "percent_digits": 1,
    "rounding": "half_up",
    "zero_denominator": "0 (-)",
    "footnote": false
  },
  "drilldown": {
    "enabled": true,
    "analysis_cells": true,
    "population_headers": true
  },
  "targets": {
    "wide": "work.shift_table",
    "long": "work.shift_table_long"
  }
}
```

JSON 必须保存 reusable user ordering，不把 Python run 的偶然 SQLite 返回顺序当成 contract。

## 16. SAS Code Generator

### 16.1 边界

新增：

```text
clinical_data_viewer/codegen/sas/shift_table_generator.py
clinical_data_viewer/codegen/sas/templates/shift_table.sas.j2
```

Generator 只负责：

- 验证 JSON type/version/reference engine；
- 准备 SAS-safe member、variable、literal 和短 token；
- 使用现有 Filter AST renderer；
- 将 ordered levels、labels 和 display rules传给模板；
- 渲染 Jinja2。

禁止在 Python 中通过大量 `lines.append()` 拼接 SAS。

### 16.2 可读 SAS 结构

模板按以下段落生成：

```text
/* 1. Prepare analysis source */
/* 2. Prepare population N */
/* 3. Calculate analyzable denominators */
/* 4. Count shift cells */
/* 5. Calculate row, column and grand totals */
/* 6. Add optional overall treatment */
/* 7. Apply labels, order and display */
/* 8. Create final wide and long datasets */
/* 9. Cleanup intermediate datasets */
```

命名使用短且可读的 SAS names，例如：

```text
src
pop
denom
cell
total
long
final
parm_ord
visit_ord
trt_ord
grade_ord
```

不使用机器化长变量名，不引入复杂宏框架。Parameter、Visit、Treatment 和 Grade labels
集中在一处维护。相同的 `set`、`keep`、`length` 和 `label` 不重复写入多个 DATA step。

最终 `work.shift_table` 和 `work.shift_table_long` 不得被 cleanup 删除。

### 16.3 Python/SAS parity

SAS 必须与 Python 保持：

- 相同 Filter AST；
- 相同 Analysis Flag；
- 相同 raw ordered levels；
- 相同 distinct subject count；
- 相同 denominator；
- 相同 missing grade exclusion；
- 相同 warning-related duplicate semantics；
- 相同 independent Total；
- 相同 `ROUND_HALF_UP` 和 percent digits；
- 相同 final row/column order。

不支持的 source kind、Merge contract 或 JSON version 必须明确拒绝，不生成近似程序。

## 17. Controller、AnalysisPanel 与 MainWindow

新增独立 Controller：

```text
clinical_data_viewer/controllers/analysis/shift_table.py
```

它拥有：

- Analysis/Population source bindings；
- Engine；
- SAS Generator；
- run/completed/failed lifecycle；
- wide/long result contexts；
- duplicate warning confirmation；
- analysis/population drilldown；
- Shell Preview；
- temporary result retention/release。

`AnalysisController` 只创建 Controller、连接 Builder signals 和暴露短入口。

`MainWindow.show_shift_table_builder()` 只负责：

- 转发到 `AnalysisController`；
- 显示 Analysis Dock；
- 应用统一 Dock 默认宽度。

Gallery 打开 Builder 必须路由到 `MainWindow.show_shift_table_builder()`，不能直接激活内部 tab，
否则 Analysis Dock 隐藏时用户看不到 Builder。

## 18. Shell 与 Gallery

施工时创建：

```text
docs/shift-table-shell.docx
clinical_data_viewer/assets/shells/shift_table.docx
clinical_data_viewer/assets/shells/shift_table.png
```

要求：

- `docs/shift-table-shell.docx` 是唯一可编辑源；
- packaged DOCX 与源 DOCX byte-identical；
- PNG 从最终 DOCX 渲染；
- 黑白三线表，顶线、header line、底线完整且粗细一致；
- 不保留参考图灰色 Discordant/Kappa 区域；
- 不显示 footnote；
- Gallery preview 裁掉无内容白边，但不降低清晰度；
- Gallery preview 使用适配窗口的高质量缩放；
- Gallery card 支持 `Safety` 和 `Categorical` tag 筛选；
- 点击 preview 放大，用户确认后才进入 Builder；
- packaged resources 由现有 package data 和 PyInstaller shell-assets 规则收集。

## 19. 文件施工清单

新增：

```text
clinical_data_viewer/shift_table/
    __init__.py
    models.py
    engine.py
    result_store.py
    configuration.py
    drilldown.py

clinical_data_viewer/ui/shift_table_builder.py
clinical_data_viewer/controllers/analysis/shift_table.py
clinical_data_viewer/codegen/sas/shift_table_generator.py
clinical_data_viewer/codegen/sas/templates/shift_table.sas.j2

docs/shift-table-shell.docx
clinical_data_viewer/assets/shells/shift_table.docx
clinical_data_viewer/assets/shells/shift_table.png

tests/test_shift_table.py
tests/test_shift_table_configuration.py
tests/test_shift_table_builder.py
tests/test_shift_table_sas_codegen.py
tests/test_shift_table_drilldown.py
tests/test_shift_table_shell.py
```

增量接线：

```text
clinical_data_viewer/domain.py
clinical_data_viewer/ui/analysis_panel.py
clinical_data_viewer/ui/table_shell_gallery.py
clinical_data_viewer/controllers/analysis_controller.py
clinical_data_viewer/controllers/analysis/__init__.py
clinical_data_viewer/codegen/__init__.py
clinical_data_viewer/codegen/sas/__init__.py
clinical_data_viewer/main_window.py
clinical_data_viewer/analysis_contracts.py       # 以仓库实际 registry 文件为准
README.md
README.en.md
```

若实际 registry 文件名不同，施工前通过 `rg` 定位现有 module contract registry，不新建第二套 registry。

## 20. 测试计划

### 20.1 Models、validation 与 ordering

1. Required variables validation；
2. Filter compilation；
3. Analysis Flag typed value；
4. Multiple Parameter selection；
5. Parameter user order preserved；
6. Visit user order preserved inside every Parameter；
7. Treatment user order preserved；
8. Custom numeric grade order；
9. Custom character grade order；
10. Duplicate raw/order validation；
11. Unknown nonmissing grade blocked；
12. Percent digits `0–4` boundary；
13. Invalid source/population binding；
14. Analysis/Population treatment incompatibility。

### 20.2 Python reference semantics

1. Detail cell distinct-subject count；
2. Record count is not used；
3. Parameter × Visit × Treatment denominator；
4. Denominator requires both grades nonmissing；
5. Baseline missing excluded and summarized；
6. Post grade missing excluded and summarized；
7. Row Total independently recalculated；
8. Column Total independently recalculated；
9. Grand Total independently recalculated；
10. Overall Total independently recalculated across Treatment；
11. Overall Total disabled；
12. Header Population N per Treatment；
13. Header Total N independently distinct；
14. Header N stable across Parameter/Visit；
15. Duplicate analysis key warning state；
16. Continue preserves all source rows；
17. Duplicate subjects remain distinct within each cell；
18. Duplicate subjects may contribute to multiple cells；
19. Zero denominator display；
20. ROUND_HALF_UP at percent boundary；
21. Multiple Parameters generate vertical sections；
22. Multiple Visits generate vertical sections, not columns；
23. Parameters missing one Visit do not create an empty matrix；
24. Numeric grade grouping uses raw numeric values；
25. Display labels do not change grouping。

### 20.3 Result store 与 drilldown

1. Wide and long result kinds；
2. Explicit order fields drive result order；
3. Detail cell source rows；
4. Row Total universe；
5. Column Total universe；
6. Grand Total universe；
7. Overall Total omits single-treatment condition；
8. Duplicate records visible in Query Result；
9. Treatment Header Population drilldown；
10. Overall Header Population drilldown；
11. Double-click and context menu use same signal；
12. Result WHERE/sort/paging does not break cell lookup；
13. Stale source/result context gives clear error；
14. temporary Query Results are released correctly。

### 20.4 JSON 与 SAS codegen

1. JSON v1 roundtrip；
2. Filter AST serialization/rendering；
3. Multiple ordered Parameters；
4. Ordered Visits inside every Parameter；
5. Ordered Treatments and custom grades；
6. Distinct-subject SQL；
7. Complete-grade denominator；
8. Independent matrix totals；
9. Optional Overall Total；
10. Population N from independent dataset；
11. Population and Analysis treatment variable names may differ；
12. Percent digits `0–4`；
13. Human-readable short SAS names；
14. Labels centralized；
15. Jinja2 template used, no large Python SAS assembly；
16. Final datasets survive cleanup；
17. Unsupported JSON/source/Merge rejected；
18. Python/SAS semantic fixtures for representative matrices。

### 20.5 UI、Gallery 与 Shell

1. Builder horizontal and vertical scrolling；
2. All controls/buttons visible at default Dock width；
3. Add/Remove/Up/Down buttons clickable；
4. ComboBox/SpinBox ignore mouse wheel；
5. source close warning preserves inputs；
6. Clear is the only normal input reset；
7. busy state and codegen enablement；
8. duplicate warning `Go Back` / `Continue`；
9. missing exclusion summary visible；
10. hidden Analysis Dock → Gallery → Open Builder shows Dock；
11. Gallery Safety/Categorical tags filter card；
12. Preview scales to window without losing source resolution；
13. source DOCX and packaged DOCX byte-identical；
14. PNG signature and resource loading；
15. rendered shell has intact three lines and no gray block/footnote；
16. packaged application includes DOCX and PNG。

### 20.6 Regression

完成后不能只运行新增测试。至少运行：

- Shift Table 全部新增 tests；
- Filter/WHERE regression；
- DatasetTab drilldown/context-menu regression；
- AnalysisController/MainWindow routing regression；
- Table Shell Gallery and shell resource regression；
- Baseline Characteristics、PROC MEANS Vertical、Change from Baseline 等相邻模块 tests；
- 全量 test suite；
- Ruff/项目现有格式检查；
- `git diff --check`。

## 21. 分阶段施工顺序

### Phase 1：Shell 与 domain contract

1. 根据本文生成 Shift Table DOCX 三线表；
2. 目视检查后渲染并裁剪 PNG；
3. 建立 immutable models、validation 和 JSON schema tests；
4. 不接入旧模块业务逻辑。

### Phase 2：Python reference 与 result

1. 实现 source filtering、flag、ordered levels；
2. 实现 duplicate/missing/unconfigured-level validation；
3. 实现 denominator、cell、matrix totals 和 Overall Total；
4. 实现 long/wide result、cell map；
5. 完成 Python semantic tests。

### Phase 3：Builder、Controller 与 drilldown

1. 实现 Builder UI 和 click-only controls；
2. 增加独立 Controller；
3. 接入 AnalysisPanel/AnalysisController/MainWindow；
4. 实现 analysis 与 population drilldown；
5. 验证 source/result lifecycle。

### Phase 4：SAS Jinja2 Generator

1. 冻结并写出 JSON v1 fixtures；
2. 实现 generator validation/context；
3. 实现可读 Jinja2 template；
4. 进行 Python/SAS parity fixtures；
5. 检查最终 datasets 不被 cleanup。

### Phase 5：Gallery、文档与回归

1. 注册 Gallery card、tags 和 preview；
2. 从 Gallery 经 MainWindow 路由打开 Builder；
3. 更新 README 中英文使用方法、边界和项目结构；
4. 运行相邻 regression 和全量测试；
5. 记录实测命令、pass/fail/skip 和已知限制。

## 22. 最终验收标准

1. 用户可以选择多个 Parameter，并按用户顺序纵向展示；
2. 每个 Parameter 内按用户 Visit 顺序纵向展示多个矩阵；
3. Treatment 只横向展开，顺序可配置；
4. Grade levels、labels 和顺序完全可配置；
5. `n` 是 distinct subjects，不是 records；
6. 百分比分母是当前 Parameter/Visit/Treatment 下 complete-grade distinct subjects；
7. Header N 来自独立 Population Dataset，并与分析分母分离；
8. Matrix totals 和 Overall Total 均独立重新计算；
9. Missing grades 不显示但排除情况对用户可见；
10. 重复分析记录先 warning，继续后不擅自挑选记录；
11. Wide/Long Results 的顺序稳定且可审阅；
12. Detail、Total、Overall 和 Header Population N 均可 drilldown；
13. Python reference 与 generated SAS contract 一致；
14. SAS 使用 Jinja2，代码短名、分段清晰、便于人工 review；
15. Shell DOCX/PNG 同源并正确进入 Gallery 和打包；
16. Gallery 在 Analysis Dock 隐藏时仍能正确显示 Builder；
17. 现有分析模块业务逻辑和公开 contract 不受影响；
18. 相关 regression 与全量测试完成并如实记录结果。

## 23. 明确延期项

以下不属于 v1：

- Treatment Down Rows；
- Missing grade 行/列；
- Discordant summary；
- Kappa statistics and confidence interval；
- 自动推导 maximum post-baseline grade；
- 自动 improvement/worsening 分类；
- footnotes、RTF/PDF、page break、spanning style system；
- arbitrary SAS code input；
- Merge source SAS code generation，除非另行冻结 merge contract。

