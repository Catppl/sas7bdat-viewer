# Categorical Table JSON v1 与 SAS Code Generator 设计

设计日期：2026-08-29

## 1. 目标

本设计为现有 `Categorical Table Builder` 建立唯一、稳定的业务配置：

```text
Categorical Builder
        ↓
CategoricalConfig
        ↓
Python CategoricalEngine
        ↓
categorical_config.json v1
        ↓
SasCategoricalGenerator
        ↓
可读、可 QC、可人工修改的 SAS 程序
```

JSON 决定“算什么”，SAS Generator 只决定“如何用 SAS 表达”。Generator
不得重新猜测：

- Numerator WHERE
- Item 和 Context Variables
- Count 类型
- Treatment 变量和列顺序
- Denominator 类型和独立 Filter
- Missing Level 规则
- Total 规则
- 百分比与舍入规则

本文件同时作为 JSON contract 与 SAS Generator v1 的实现基线。

## 2. 设计原则

1. JSON 保存业务语义，不保存 SQLite SQL、临时 SQLite 路径或 SAS 临时表名。
2. 所有 Filter 保存原始文本和现有 Filter AST；SAS Generator 从 AST 渲染。
3. Source 和 Population 的变量 metadata 全量保存，避免 Generator 根据变量名猜类型或格式。
4. Treatment levels 使用当前打开数据实际解析出的固定顺序。SAS code 不再动态扫描 Treatment；数据或 Treatment arms 改变后应重新生成 code。
5. Item levels 仍在 SAS 运行时从数据发现，避免为 RACE、AVISIT 等每个 level 生成大量硬编码语句。
6. Item 顺序严格使用 Builder 数组顺序。
7. SAS 临时对象命名属于 Generator 渲染规则，不进入业务 JSON。
8. Merge Result 可以运行 Python Categorical Table 并生成 JSON，但 SAS Generator v1 必须拒绝 `input.kind = "merge"`。
9. 生成代码的可读性基线不是重新设计一套风格，而是当前已经通过人工审阅的
   `rule_based.sas.j2`：显式 Treatment 条件计数、短而有意义的 WORK 成员、按业务
   阶段分块、适量注释，以及统一在末尾构造 `col1-colN`。

## 3. 顶层 schema

Categorical JSON v1 顶层固定为：

```json
{
  "type": "categorical_table",
  "version": 1,
  "input": {},
  "variables": {},
  "numerator": {},
  "items": [],
  "count": {},
  "treatment": {},
  "denominator": {},
  "total": {},
  "sort": {},
  "calculation": {},
  "display": {},
  "output": {},
  "targets": {}
}
```

字段名不应由 Generator 自行改名。

## 4. Population N 完整示例

```json
{
  "type": "categorical_table",
  "version": 1,

  "input": {
    "kind": "sas",
    "format": "sas7bdat",
    "dataset": "ADAE",
    "source_path": "C:\\project\\data\\adae.sas7bdat",
    "source_directory": "C:\\project\\data"
  },

  "variables": {
    "USUBJID": {
      "type": "character",
      "label": "Unique Subject Identifier",
      "length": 20,
      "format": ""
    },
    "TRTA": {
      "type": "character",
      "label": "Actual Treatment",
      "length": 40,
      "format": ""
    },
    "TRTEMFL": {
      "type": "character",
      "label": "Treatment Emergent Analysis Flag",
      "length": 1,
      "format": ""
    },
    "AESEV": {
      "type": "character",
      "label": "Severity/Intensity",
      "length": 20,
      "format": ""
    },
    "AESER": {
      "type": "character",
      "label": "Serious Event",
      "length": 1,
      "format": ""
    }
  },

  "numerator": {
    "filter": {
      "language": "sas_like",
      "text": "TRTEMFL = \"Y\"",
      "ast": {}
    }
  },

  "items": [
    {
      "variable": "AESEV",
      "label": "Maximum Severity",
      "context_variables": [],
      "missing_level": {
        "include": false,
        "label": "(Missing)"
      },
      "level_order": {
        "method": "runtime_value_ascending"
      }
    },
    {
      "variable": "AESER",
      "label": "Serious TEAE",
      "context_variables": [],
      "missing_level": {
        "include": false,
        "label": "(Missing)"
      },
      "level_order": {
        "method": "runtime_value_ascending"
      }
    }
  ],

  "count": {
    "type": "distinct_subjects",
    "subject_variable": "USUBJID",
    "subject_missing": "exclude"
  },

  "treatment": {
    "source_variable": "TRTA",
    "missing_policy": "error",
    "level_order": "resolved",
    "resolved_levels": [
      {
        "value": "Placebo",
        "label": "Placebo"
      },
      {
        "value": "Drug A",
        "label": "Drug A"
      }
    ]
  },

  "denominator": {
    "type": "population",
    "population": {
      "input": {
        "kind": "sas",
        "format": "sas7bdat",
        "dataset": "ADSL",
        "source_path": "C:\\project\\data\\adsl.sas7bdat",
        "source_directory": "C:\\project\\data"
      },
      "variables": {
        "USUBJID": {
          "type": "character",
          "label": "Unique Subject Identifier",
          "length": 20,
          "format": ""
        },
        "TRT01A": {
          "type": "character",
          "label": "Actual Treatment",
          "length": 40,
          "format": ""
        },
        "SAFFL": {
          "type": "character",
          "label": "Safety Population Flag",
          "length": 1,
          "format": ""
        }
      },
      "treatment_variable": "TRT01A",
      "filter": {
        "language": "sas_like",
        "text": "SAFFL = \"Y\"",
        "ast": {}
      }
    }
  },

  "total": {
    "enabled": true,
    "method": "recompute_from_analysis_universe"
  },

  "sort": {
    "items": "configured_order",
    "contexts": {
      "method": "runtime_value_ascending",
      "character_collation": "case_insensitive",
      "numeric_order": "numeric",
      "missing": "last"
    },
    "levels": {
      "method": "runtime_value_ascending",
      "character_collation": "case_insensitive",
      "numeric_order": "numeric",
      "missing": "last"
    }
  },

  "calculation": {
    "reference_engine": "python_categorical_v1",
    "numerator_filter_scope": "source_only",
    "denominator_filter_scope": "independent",
    "item_filter_applies_to_denominator": false,
    "percent_method": "freq_divided_by_denom_times_100",
    "zero_denominator_percent": null,
    "total_method": "recompute_from_analysis_universe"
  },

  "display": {
    "percent_digits": 1,
    "rounding": "half_up",
    "zero_denominator_display": "0 (—)",
    "level_indent_spaces": 4,
    "header_rows": true
  },

  "output": {
    "layout": "wide_and_long",
    "wide": {
      "item_column": "item",
      "item_label": "Event",
      "treatment_column_pattern": "col{index}",
      "treatment_label_pattern": "{label} n (%)"
    },
    "long": {
      "columns": [
        "item_order",
        "item_variable",
        "item_label",
        "context",
        "level",
        "treatment",
        "trt_order",
        "freq",
        "denom",
        "pct"
      ]
    }
  },

  "targets": {
    "sas": {
      "source_library": "analysis",
      "source_member": "adae",
      "population_library": "pop",
      "population_member": "adsl",
      "output_dataset": "work.cat_result",
      "long_output_dataset": "work.cat_long"
    }
  }
}
```

示例中的 `ast` 仅为占位；实际 JSON 必须调用现有
`serialize_filter_ast()` 生成，不得手工构造。

## 5. Non-missing N denominator

Non-missing denominator 固定为：

```json
{
  "denominator": {
    "type": "nonmissing",
    "analysis_value_variable": "AVAL",
    "base_filter": "numerator.filter"
  }
}
```

业务语义：

```text
Source
+ Numerator WHERE
+ 当前 Item 的 Context combination
+ AVAL nonmissing
→ denominator
```

Item level 条件不进入 denominator。

当 `count.type = "distinct_subjects"` 时，计算 distinct Subject；当
`count.type = "records"` 时，计算 records。

## 6. Baseline + Postbaseline n1 denominator

n1 固定使用 record count。JSON 建议为：

```json
{
  "count": {
    "type": "records",
    "subject_variable": "USUBJID",
    "subject_missing": "exclude_for_eligibility"
  },
  "denominator": {
    "type": "baseline_postbaseline",
    "analysis_value_variable": "AVAL",
    "baseline_filter": {
      "language": "sas_like",
      "text": "ABLFL = \"Y\"",
      "ast": {}
    },
    "postbaseline_filter": {
      "language": "sas_like",
      "text": "ABLFL != \"Y\" and AVISITN > 0",
      "ast": {}
    },
    "eligibility": {
      "base_filter": "numerator.filter",
      "match_variables": "treatment_subject_and_item_context",
      "baseline_analysis_nonmissing": true,
      "postbaseline_analysis_nonmissing": true,
      "numerator_source": "eligible_postbaseline_records",
      "denominator_source": "eligible_postbaseline_records"
    }
  }
}
```

`match_variables` 的实际值必须由：

```text
Treatment Variable
+ Subject Variable
+ 当前 Item 的 Context Variables
```

生成。`PARAMCD / AVISITN` 等 Context 不能硬编码。为了避免 JSON 同时保存推导规则
和可能不一致的展开值，正式 v1 只保存：

```json
"match_variables": "treatment_subject_and_item_context"
```

Generator 根据同一 JSON 中的 Treatment、Subject 和当前 Item Context 展开，并在
validation 中验证展开结果。

## 7. Count contract

现有 Python model 到 JSON v1 的映射固定为：

```text
CategoricalConfig.count_type == "distinct_subject" → "distinct_subjects"
CategoricalConfig.count_type == "record"           → "records"
```

JSON 使用复数形式作为跨语言 contract；Configuration Builder 负责做上述显式映射，
Generator 不接受 Python model 的旧枚举值。

### Distinct subjects

```json
{
  "type": "distinct_subjects",
  "subject_variable": "USUBJID",
  "subject_missing": "exclude"
}
```

对应：

```sas
count(distinct usubjid)
```

并显式排除 character blank / missing Subject。

### Records

```json
{
  "type": "records",
  "subject_variable": "USUBJID",
  "subject_missing": "not_applicable"
}
```

普通 Population / Non-missing denominator 使用 `count(*)`。n1 虽然最终统计
records，但 Subject 仍用于 baseline/postbaseline eligibility，因此 n1 使用
`exclude_for_eligibility`。

## 8. Treatment contract

Treatment 不在生成的 SAS 中动态发现。生成 JSON 时从当前 source 和独立
population universe 合并、校验和排序，然后保存真实类型：

```json
"resolved_levels": [
  {"value": 0, "label": "Placebo"},
  {"value": 1, "label": "Drug A"}
]
```

- character value 保持 JSON string。
- numeric value 保持 JSON number。
- 不允许 missing level；`missing_policy = "error"`。
- 数组顺序就是 `col1 ... colN` 的顺序。
- Total 启用时永远位于最后一个 `colN`，但不加入 `resolved_levels`。

这样可以生成可读的显式 SAS：

```sas
count(distinct case when trta = 'Placebo' then usubjid end) as count1,
count(distinct case when trta = 'Drug A' then usubjid end) as count2
```

而不是生成复杂的动态 Treatment macro。Treatment arms 改变后，用户应重新生成
JSON 和 SAS code。

## 9. Item、Context 和 Level 顺序

- Item：严格使用 `items` array 顺序。
- Context Variables：严格使用每个 Item 的 `context_variables` array 顺序。
- Context combinations：运行时按 raw value 排序；character 不区分大小写，numeric
  按数值排序，missing 最后。
- Levels：在每个 Context combination 内按相同规则排序。
- Missing level：仅当 `missing_level.include = true` 时输出，显示为 `(Missing)`。

原 Python Engine 使用 `context_json / level_json` 字符串排序，numeric level `10`
可能排在 `2` 前。本次已经改为 metadata-driven、类型感知的 raw-value 排序；SAS
模板通过 missing/key/raw 三层 runtime sort key 实现同一 `sort` block，Generator
不得自行猜测另一套顺序。

## 10. Total contract

Total 永远重新计算：

- distinct subjects：跨 Treatment 重新 count distinct Subject。
- records：跨 Treatment 重新 count records。
- Population：从独立 Population Filter universe 重新计算。
- Non-missing：从 Numerator WHERE + analysis nonmissing universe 重新计算。
- n1：从 eligible postbaseline records 重新计算。

禁止把各 Treatment 列相加作为 Total。

## 11. SAS 人类可读命名规范

JSON 不保存临时表名。`SasCategoricalGenerator` 应统一使用以下短且有意义的名称：

| 用途 | 推荐名称 |
|---|---|
| Filter 后 source | `cat_src` |
| Filter 后 population | `pop_src` |
| Treatment map | `trt_map` |
| Item numerator | `num_race`, `num_aesev` |
| Item denominator | `den_race`, `den_aesev` |
| n1 baseline | `base_anrind` |
| n1 postbaseline | `post_anrind` |
| n1 eligible rows | `elig_anrind` |
| Item rows | `row_race`, `row_aesev` |
| Combined long result | `cat_long` |
| Final wide result | `cat_result` |

内部变量推荐：

```text
item_ord
ctx_ord
level_ord
trt_ord
item
level
trt
subjid
freq
denom
pct
display
count1-countN
denom1-denomN
col1-colN
```

不要使用：

```text
categorical_treatment_frequency_intermediate_dataset
__cde_categorical_denominator_count
categorical_analysis_value_variable
```

Item 变量名用于生成临时成员时必须转为安全的小写 token，并保证 SAS member 不超过
32 characters。非标准变量引用继续使用共享 `sas_name` helper。

## 12. 推荐 SAS 程序结构

使用 Jinja2：

```text
clinical_data_viewer/codegen/sas/categorical_generator.py
clinical_data_viewer/codegen/sas/templates/categorical_table.sas.j2
```

生成代码按以下 section 排列：

```sas
/* Prepare source data */
/* Prepare population denominator */
/* Validate treatment values */
/* Item: RACE */
/* Item: AESEV */
/* Combine long results */
/* Build final table */
/* Clean up */
```

代码应优先使用普通、可读的：

- DATA step
- PROC SQL
- PROC SORT
- PROC TRANSPOSE
- 少量简单 array

不要生成复杂 macro framework。每个 Item 可以形成一段独立、带 Item 名注释的代码，
便于 SAS programmer 单独核查 numerator 和 denominator。

### 12.1 Rule-based 代码风格是正式参考基线

Categorical Generator 应直接沿用当前 Rule-based Generator 的阅读顺序：

```text
program header
→ prepare filtered source / population
→ validate missing treatment
→ calculate denominator once
→ calculate each configured item independently
→ combine item rows
→ calculate n (%) once
→ label and keep final columns
→ clean up intermediate WORK tables
```

需要复用的具体风格包括：

- Header 只列 Source、Treatment、Count、Denominator、Total 和 Percent digits 等关键信息。
- `cat_src` / `pop_src` / `denom` / `row_race` 等名称看到即可理解用途。
- 每个 Item 一段 `/* Item 1: RACE */`，不把所有 Item 塞进难以阅读的宏循环。
- Treatment 使用 JSON 中已经 resolved 的 levels，生成显式 `case when`。
- 一项 count expression 占一行；不把完整 `SELECT` 压成一行，也不把一个表达式拆成
  过多碎片。
- 分母只计算一次并明确注明 Item/Level 条件不会进入 denominator。
- `n (%)` 集中在一个 DATA step 中构造，继续使用 Rule-based 的 `cat()`、`cnt{i}`
  使用 `3.`、动态百分比宽度 `4 + digits`，以及 `item` label `Event` 的风格。
- 最后只保留面向用户的列；内部审计数据写入独立 long result。

Categorical 与 Rule-based 的业务差异仍需保持：Rule-based 的 row/filter 是用户固定
定义的，而 Categorical 的 Context/Level rows 是运行时从当前数据发现的。复用的是
代码风格和公共渲染 helper，不是把 Categorical 强行改造成 Rule-based row model。

### 12.2 目标 SAS 形态示例

以下示例只说明人类可读形态；实际变量、Filter、Treatment level 和 Item 必须来自
JSON，不得硬编码：

```sas
/*
   Generated by SASDataViewer
   Categorical Table configuration version 1
   Source: analysis.adae
   Treatment: TRTA
   Count: distinct USUBJID
   Denominator: population
   Total enabled: true
   Percent digits: 1
*/
options validvarname=any;

/* Prepare source data */
libname analysis "C:\project\data";

data cat_src;
    set analysis.adae;
    if not (TRTEMFL = 'Y') then delete;
run;

/* Prepare the independent population denominator */
libname pop "C:\project\data";

data pop_src;
    set pop.adsl;
    if not (SAFFL = 'Y') then delete;
run;

/* Calculate denominators. Item and level conditions are not applied here. */
proc sql;
    create table denom as
    select
        count(distinct (case when TRT01A = 'Placebo' then USUBJID end)) as denom1,
        count(distinct (case when TRT01A = 'Drug A' then USUBJID end)) as denom2,
        count(distinct (case when not missing(TRT01A) then USUBJID end)) as denom3
    from pop_src
    where not missing(USUBJID);
quit;

/* Item 1: RACE */
proc sql;
    create table num_race as
    select
        RACE as level,
        count(distinct (case when TRTA = 'Placebo' then USUBJID end)) as count1,
        count(distinct (case when TRTA = 'Drug A' then USUBJID end)) as count2,
        count(distinct (case when not missing(TRTA) then USUBJID end)) as count3
    from cat_src
    where not missing(USUBJID)
      and not missing(RACE)
    group by RACE;
quit;

data row_race;
    if _n_ = 1 then set denom;
    set num_race;
    length item col1-col3 $200;

    item = cat('    ', strip(level));

    array cnt {*} count1-count3;
    array den {*} denom1-denom3;
    array col {*} $ col1-col3;

    do i = 1 to dim(cnt);
        if den{i} = 0 then
            col{i} = '0 (—)';
        else do;
            _pct = cnt{i} / den{i} * 100;
            col{i} = cat(
                strip(put(cnt{i}, 3.)),
                ' (',
                strip(put(round(_pct, 0.1), 5.1)),
                ')'
            );
        end;
    end;

    label
        item = 'Event'
        col1 = 'Placebo n (%)'
        col2 = 'Drug A n (%)'
        col3 = 'Total n (%)'
    ;
run;
```

正式模板还需要为每个 Item 生成 header row、Context columns 的运行时组合、Long
Result、Missing Level，以及 Non-missing/n1 的对应分母逻辑。即使这些分支增加，
每一段仍应保持上述 Rule-based 风格，而不是退回 Python 中逐行拼接 SAS 字符串。

### 12.3 模板与共享 helper

实现文件：

```text
clinical_data_viewer/codegen/sas/categorical_generator.py
clinical_data_viewer/codegen/sas/templates/categorical_table.sas.j2
```

并复用现有：

- Jinja `Environment` 配置；
- `sas_filter_expression()`；
- `sas_name()` / `sas_string()`；
- Rule-based 的 SAS literal、dataset reference 和短 WORK member validation 规则。

不建议直接 include 整个 `rule_based.sas.j2`，因为两类表的 row discovery 不同；可将
真正完全一致的 `n (%)` display 片段轻量抽成 shared partial，但只有在两侧生成结果
完全相同时才抽取，避免为了复用制造新的抽象层。

## 13. JSON 文件生命周期

Categorical 成功运行后，在 Result temporary directory 中保存：

```text
dataset.sqlite
categorical_config.json
```

- 与 Result Tab 同生命周期。
- 不写入 source dataset directory。
- 不保存 Viewer SQLite cache path。
- UTF-8、`ensure_ascii=False`、`indent=2`、文件末尾 newline。
- JSON 直接由 `CategoricalConfig + DatasetHandle + resolved treatment levels`
  构建，不从 `categorical_long` 反推。

## 14. 建议 API

新增：

```text
clinical_data_viewer/categorical/configuration.py
```

职责：

```python
build_categorical_configuration(
    source,
    config,
    population=None,
    resolved_treatment_levels=(),
)

categorical_configuration_json(configuration)

write_categorical_configuration(
    path,
    source,
    config,
    population=None,
    resolved_treatment_levels=(),
)
```

Generator 只接收 JSON-compatible `dict[str, object]`，不直接接收
`CategoricalConfig`、`DatasetHandle`、SQLite SQL 或 `CategoricalEngine`。

## 15. Validation 要求

JSON Builder 至少验证：

- `type == "categorical_table"`
- `version == 1`
- source kind / format 合法
- 所有 Item / Context / Treatment / Subject / Analysis Value 变量存在
- referenced variables metadata 完整
- Filter 可由现有 FilterEngine compile，AST 可 serialize
- Count type 与 denominator type 兼容
- n1 强制 record count
- Population source、Treatment 和 Context metadata 完整且类型兼容
- Treatment resolved levels 非 missing、类型正确、顺序稳定且无重复
- percent digits 为 0–4
- output 和 targets 使用合法 SAS reference

## 16. Tests 设计

### Configuration tests

- 顶层字段固定、type/version 正确
- source `.sas7bdat` / `.xpt` / merge 标记
- variables metadata 完整
- Numerator Filter text + AST
- Item 顺序、label、context、missing level
- distinct subjects / records count
- character / numeric Treatment resolved levels
- Population / Non-missing / n1 三种 denominator
- Population Filter 与 Numerator Filter 独立
- Baseline / Postbaseline Filter text + AST
- Total / display / output / targets
- UTF-8 JSON roundtrip 和 newline

### Python parity tests

- duplicate Subject 不重复计数
- record count 不去重
- Context denominator 独立
- missing item level include/exclude
- Population Treatment 变量与 source 名称不同
- Non-missing denominator 应用 Numerator WHERE + analysis nonmissing
- n1 eligibility 使用 Treatment + Subject + 当前 Item Context
- Total 重新计算，不是 Treatment sum
- numeric Context / Level 排序为 numeric，不是 JSON string 排序

### Future SAS Generator tests

- Generator 只接 JSON dict
- Merge source 被明确拒绝
- 三种 denominator 的可读 SAS
- Filter 全部从 AST 渲染
- Treatment literals 类型正确
- Item 临时表名简短且稳定
- 最终输出为 `item + col1-colN`
- Total 永远是最后一列
- SAS code 不含 `__cde` 或机器式长名称
- Jinja2 template 被正常加载

## 17. 已确认的排序行为

JSON v1 推荐将 Context / Level 排序定义为类型感知的 raw-value ascending。这比当前
`context_json / level_json` 字符串排序更符合临床 review，也更容易用 SAS 复现，但会让
少数 numeric level 的顺序发生修正。

本次已经确认并实施：

```text
numeric levels: 2 before 10
character levels: case-insensitive ascending
missing level: last
```

除这项排序对齐外，设计不要求修改现有 Categorical calculation semantics。
