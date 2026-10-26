# Change from Baseline（Horizontal）施工方案

本文档记录 Change from Baseline 横版 Builder 的已确认需求和实施边界。

本方案已获批准并进入第一版施工；本文同时作为实现边界和验收记录。

## Shell Preview 资源

横版 shell 的可维护源文件保留在：

```text
docs/change-from-baseline-horizontal-shell.docx
```

运行时预览只展示 PNG，不打开 DOCX：

```text
clinical_data_viewer/assets/shells/change_from_baseline_horizontal.png
```

对应的 DOCX 也随应用资源打包，便于维护和追溯。更新 DOCX 后，应重新渲染 PNG，并同步替换上述两个资源；Builder 的 `Shell Preview…` 只读取 PNG。

## 1. 入口与模块边界

新增入口：

```text
Tools > Change from Baseline (Horizontal)
```

新增独立模块，不把计算业务逻辑写入 `MainWindow`：

```text
clinical_data_viewer/change_from_baseline/
    __init__.py
    models.py
    engine.py
    result_store.py
    configuration.py
    # v1 不新增单元格 drilldown；结果本身可直接浏览

clinical_data_viewer/ui/change_from_baseline_builder.py
clinical_data_viewer/controllers/analysis/change_from_baseline.py
```

`MainWindow` 只负责菜单入口、Builder 路由、Controller 注册、worker 和 Result Tab 容器协调。

不修改现有 PROC MEANS、Categorical、Rule-based、AE Table 的业务逻辑。可以复用现有 PROC MEANS 的统计定义、decimal 处理、FilterEngine、SQLite result lifecycle 和公共 helper；如果现有 helper 不是公共 API，不直接改动旧模块，优先在新模块中增加适配层。

## 2. Source Dataset

Builder 打开后由用户从当前已打开的数据集中选择 Source Dataset，不强锁当前 Viewer 数据集。

如果没有已打开的数据集，显示英文提示：

```text
Please open a dataset before using Change from Baseline Builder.
```

Source 切换时：

- 重新加载变量列表和 metadata；
- 不把旧 source 的 filter 或变量选择泄漏到新 source；
- 用户点击 Clear 才清空 Builder 配置；
- source 被关闭时保留配置，但禁止运行并提示用户重新选择 source。

## 3. 核心输入

### 3.1 Change Variable

Change 值直接来自 source dataset 中已有的变量，例如：

```text
CHG
```

Builder 通过变量选择框让用户选择 Change Variable。

第一版不重新计算：

```text
Postbaseline Value - Baseline Value
```

不执行 baseline join，不根据变量名猜测 `CHG`、`BASE` 或 `AVAL` 的语义。

### 3.2 Item Variable

第一版只支持一个 Item Variable。

例如：

```text
Item Variable: PARAMCD
```

Item 在结果中显示为左侧第一列，列标题统一使用 `Item`，不写死为 `Score`。

Item level 的输出顺序规则：

1. `PARAMCD IN ("ALB", "ALT", "AST")`：按 `IN` literal 出现顺序；
2. `PARAMCD = "ALT" OR PARAMCD = "ALB"`：按 equality 出现顺序；
3. 其他无法安全解析顺序的复杂 WHERE：使用稳定的默认排序，并提示用户；
4. 如果后续需要覆盖复杂 WHERE 的顺序，再增加独立的 `Custom Item Order` 控件。

Item label 必须集中维护，不得散落在多个 engine、template 或统计分支中。生成的 SAS 程序未来应提供单独的集中 label block，方便按 shell 统一修改。

### 3.3 Row Variables

不固定使用 `Visit` 字段名。用户可以选择零个、一个或多个 Row Variables，例如：

```text
AVISITN
```

或：

```text
AVISITN + VISIT
```

Row Variables 的选择顺序就是结果左侧列顺序。第一版排序按 raw value：

- numeric：numeric ascending 或 descending；
- character：大小写不敏感 ascending 或 descending；
- 缺失值排在最后，除非未来增加显式 missing order。

每个 Row Variable 都应提供排序方向选择：

```text
Ascending
Descending
```

### 3.4 Treatment

Treatment 和 Row Variables 都是可选的，不假定一定存在或一定显示。

如果选择 Treatment 作为统计列分组变量，则右侧按 treatment 展开统计列；如果用户将 Treatment 作为 Row Variable，则它显示在左侧行维度中。

实现时必须避免同一个变量同时作为 Row Variable 和右侧列分组变量，或给出明确 warning，避免语义重复。

### 3.5 Filters

第一版保留两个独立过滤条件：

```text
Dataset WHERE
Postbaseline WHERE
```

两者都复用现有 FilterEngine 和 Filter AST，不新增 parser。

由于 Change Variable 已存在于 source dataset，第一版移除 Baseline WHERE 和 Baseline matching 配置。

## 4. 统计语义

统计量默认支持：

```text
N
Mean
SD
Median
Q1
Q3
Min
Max
```

统计定义尽量复用现有 PROC MEANS：

- Mean、Median、Min、Max 与现有 PROC MEANS 保持一致；
- SD 使用 sample standard deviation；
- Q1/Q3 沿用当前 QNTLDEF 规则；
- decimal digits、ROUND_HALF_UP 和显示格式沿用现有 PROC MEANS 能力。

### 4.1 小 n

图中的小 `n` 定义为：

```text
非 missing Change 记录数
```

即当前统计分组内，Change Variable 非 missing 的记录数量。

### 4.2 大 N

图中的大 `N` 定义为：

```text
distinct USUBJID 数量
```

大 N 统计当前 Item / Row / Treatment 分组中出现的 distinct、非 missing `USUBJID`。

如果同一受试者有多条 Change 记录：

- 小 n 计记录数；
- 大 N 只计一次受试者；
- 不静默去重原始记录。

### 4.3 重复 Change 记录

同一个：

```text
USUBJID + Item + Row Variables + Treatment
```

存在多条 Change 记录时：

- 弹出 warning；
- 明确显示重复记录的分组条件；
- 允许用户继续计算；
- 不自动取第一条、最后一条或平均值。

## 5. Missing 规则

- Missing `USUBJID`：不进入大 N；
- Missing Change：不进入小 n，也不参与 Mean/SD/Median/Q1/Q3/Min/Max；
- Missing Treatment：阻止计算并提示用户修改条件；
- Missing Row Variable：按统一 missing 排序规则处理，并在结果中保留可识别的 missing 显示。

## 6. 横版结果

左侧列顺序：

```text
Item
用户选择的 Row Variables（按配置顺序）
```

右侧如果存在 Treatment 列分组，则为：

```text
Treatment A: n / N / Mean / SD / Median / Q1 / Q3 / Min / Max
Treatment B: n / N / Mean / SD / Median / Q1 / Q3 / Min / Max
```

Total 如果启用，永远放在最后。

Wide result 是默认展示结果；同时保存 Long Result 供审阅、筛选、排序和 CSV 导出。

## 7. JSON v1 规划

结果临时目录保存：

```text
dataset.sqlite
change_from_baseline_config.json
```

建议顶层结构：

```json
{
  "type": "change_from_baseline_horizontal",
  "version": 1,
  "input": {},
  "dataset_filter": {},
  "postbaseline_filter": {},
  "variables": {
    "subject": "USUBJID",
    "change": "CHG",
    "item": "PARAMCD",
    "row": [],
    "treatment": null
  },
  "variable_metadata": {},
  "statistics": [
    "N",
    "MEAN",
    "SD",
    "MEDIAN",
    "Q1",
    "Q3",
    "MIN",
    "MAX"
  ],
  "item": {
    "labels": {},
    "order_source": "where_literals_or_default"
  },
  "treatment": {
    "level_order": "resolved",
    "resolved_levels": []
  },
  "total": {
    "enabled": true,
    "method": "recompute_distinct_subjects"
  },
  "calculation": {
    "reference_engine": "python_change_from_baseline_v1",
    "change_source": "existing_dataset_variable",
    "small_n": "nonmissing_change_records",
    "large_n": "distinct_subjects",
    "subject_missing": "exclude",
    "change_missing": "exclude",
    "treatment_missing": "error",
    "duplicate_change_records": "warning_continue"
  },
  "display": {
    "rounding": "half_up",
    "maximum_decimals": 4,
    "statistic_decimals": {
      "MEAN": 1,
      "SD": 2,
      "MEDIAN": 1,
      "Q1": 1,
      "Q3": 1,
      "MIN": 0,
      "MAX": 0
    }
  },
  "targets": {}
}
```

`statistic_decimals` 是全表统一的统计量显示精度，不按 PARAMCD 单独配置；`n` 和 `SUBJECT_N` 不使用小数位，所有设置最多 4 位。

## 8. SAS Generator

第一版 SAS Code Generator 已实现。入口位于 Change from Baseline Builder
底部的 `SAS Code Generator…` 按钮，当前只对真实的 SAS7BDAT source enabled。

生成程序不输出 `LIBNAME`，默认引用运行环境已经分配的 `adam` libref，例如
`adam.adlb`。XPT、Merge Result source 暂不支持 SAS code generation。

生成器消费 Change from Baseline JSON v1，并保持以下边界：

- 生成代码应参考 SAS 标准程序风格；
- Item label 统一集中在一个可编辑 block；
- 不使用复杂 macro framework；
- 使用短且有意义的 dataset / variable 名；
- 不把 label 逻辑散落在各个统计分支；
- SAS 程序使用同一份 JSON 业务配置；Treatment levels 使用当前 Builder
  snapshot 中已经解析的顺序，不在 SAS 中重新发现 treatment；
- SAS 内部使用 `PROC MEANS`、`PROC SQL` 和短而有意义的临时表名；
- Total 独立重新计算，不通过 treatment 列相加得到；
- Item label 在 source preparation 阶段集中处理，方便按 shell 修改。

## 9. 测试计划

至少覆盖：

1. 用户选择已有 Change Variable；
2. 单个 Item Variable；
3. Item `IN` 顺序；
4. Item OR equality 顺序；
5. 复杂 WHERE 的稳定 fallback；
6. Item label 集中配置；
7. 多个 Row Variables；
8. Row Variable ascending / descending；
9. Treatment 作为列分组；
10. Treatment 作为 Row Variable；
11. 小 n 为非 missing Change 记录数；
12. 大 N 为 distinct USUBJID 数；
13. 同一受试者多条 Change 记录；
14. 重复记录 warning 后继续；
15. missing Change；
16. missing USUBJID；
17. missing Treatment 阻止计算；
18. Wide Result 列顺序；
19. Long Result 记录完整；
20. JSON roundtrip；
21. Result Tab 生命周期和临时目录清理；
22. Clear 和 source 切换行为。

## 10. 当前明确不做

- 不重新计算 baseline；
- 不执行 baseline join；
- 不新增 Baseline WHERE；
- 不自动猜测 `CHG`、`BASE` 或 `AVAL`；
- 不修改现有 PROC MEANS 模块；
- 不把逻辑写入 MainWindow；
- 不实现 SAS Code Generator；
- 不自动处理重复 Change 记录；
- 不静默去重受试者记录。

