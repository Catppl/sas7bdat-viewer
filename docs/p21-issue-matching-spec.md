# P21 Issue 匹配规范

状态：第一版匹配合同与 P0 fixtures 已冻结；P2 匹配引擎已实现

更新日期：2026-11-05

## 1. 目的

本文定义如何把 P21 `Issue Details` 中的 issue 映射到每天更新的当前数据，并生成可检查的 SAS-style WHERE。

本规范优先保证召回率：不能因为 `Record`、`SEQ` 或单个值发生变化就漏掉可能仍相关的记录；同时必须清楚区分 exact、partial、not found 和无法评估。

相关文档：

- [第一版需求](p21-issue-explorer-requirements.md)
- [架构设计](p21-issue-explorer-design.md)
- [施工计划](p21-issue-explorer-implementation-plan.md)

## 2. 核心术语

- **Issue**：P21 `Issue Details` 中一条符合第一版范围的行。
- **Local variable**：属于当前 `Dataset`、可在当前 domain 中搜索的变量。
- **Cross-domain variable**：形如 `DM:RFICDTC` 的引用；第一版忽略。
- **Strict exact candidate**：除 `SEQ` 外，所有可评估 local variables 均按严格规则匹配的当前记录。
- **Review candidate**：没有 strict exact 时，达到 80% 阈值的 partial 或 lenient character candidate。
- **Expected WHERE**：按 P21 历史 issue 的全部可用本地条件生成的表达式。
- **Applied WHERE**：为了实际显示 candidate 而执行的表达式；review 搜索时可能省略一个或多个不匹配条件。
- **Current Match Count**：当前数据中的去重 candidate 数，不是 P21 issue 数。
- **Source row**：Viewer 当前数据中的 `_source_row`，用于唯一标识候选记录。

## 3. Report 读取

### 3.1 Worksheet

- 在所有 worksheets 中定位逻辑名称为 `Issue Details` 的 worksheet；比较时忽略首尾空格、大小写和不间断空格。
- 不读取或合并其他 worksheets。
- 找不到时停止导入并给出明确错误。

### 3.2 表头发现

程序在 worksheet 起始区域按必需字段集合寻找表头，不假定固定行号。建议第一版扫描前 100 行。

一行只有在必需字段全部唯一出现时才被认定为表头。字段名比较时可以忽略首尾空格和大小写，但导入后使用规范字段名。

### 3.3 第一版有效 issue

仅保留：

```text
Count is blank AND Record is not blank
```

以下内容直接标记为第一版忽略，不进入匹配：

- `Count` 有值。
- `Count` 为空且 `Record` 也为空。

每条 issue 生成内部 `issue_id`：

```text
report fingerprint + worksheet row number
```

不能把 P21 `Record` 当作当前数据行号。

## 4. Variables 分类

### 4.1 解析

`Variables` 按逗号拆分，逐项去除首尾空格，并保留原顺序。变量名按 SAS 变量名不区分大小写比较。

每个变量分类为：

1. `local_matchable`：当前 domain metadata 中存在。
2. `cross_domain_ignored`：包含 `:`，例如 `DM:RFICDTC`。
3. `local_missing`：看起来属于当前 domain，但当前 metadata 中不存在。

`local_missing` 不能直接当作值不匹配；该 issue 标为 `Cannot Evaluate`。

### 4.2 稳定定位信息

- `USUBJID` 是优先稳定定位字段。
- 其他稳定字段可包括 `STUDYID` 及 domain 中业务上稳定的标识。
- 名称以 `SEQ` 结尾的 sequence 变量第一版完全不参与匹配，不进入 strict exact、80% 分母或 WHERE。
- `SEQ` 对应的 P21 value 仍需在 Values 对齐时消费，避免后续变量和值错位。
- `Record` 只用于追溯 P21 report，不参与当前数据定位。

## 5. Values 映射

### 5.1 禁止直接按逗号等长拆分

`Variables` 中的逗号是字段分隔符，但字符型 `Values` 本身也可能包含逗号。因此禁止使用：

```text
split(Values, ',') then zip(Variables, Values)
```

否则会把一个字符值错误拆成多个变量值。

### 5.2 类型信息

值映射和比较以当前数据集 metadata 为准：

- 当前变量为 character：按字符规则处理。
- 当前变量为 numeric：按数值规则处理。
- `--DTC` 等日期时间变量在当前数据中为 character 时，按字符值匹配，不转换为数值日期。
- metadata 缺失时不猜测类型。

### 5.3 映射策略

匹配器采用两阶段策略：

#### 阶段 A：确定性快速路径

当值数量与可映射变量数量一致，且每个 token 均满足相应类型约束时，建立唯一映射并继续匹配。

#### 阶段 B：受约束动态对齐

当 `Values` 中存在额外逗号时：

1. 为所选 domain/Rule ID 的相关变量，从 snapshot 建立规范化 distinct-value index。
2. numeric 变量只能消费一个 token，且 token 必须能解析为数值或缺失。
3. character 变量可以消费一个或多个连续 token，重新以逗号连接。
4. cross-domain variable 消费其对应历史片段，但不生成本地搜索条件。
5. `SEQ` variable 消费其对应历史片段，但不生成本地搜索条件。
6. 使用变量类型、当前 distinct values 和完整记录候选共同约束分段。
7. 保留所有有效映射，不因找到第一种解释就停止。

如果仍有多个值映射：

- 对每种映射分别搜索。
- candidate 按 `_source_row` 去重。
- issue 标记 `Ambiguous Value Mapping`，交给用户检查。
- 不自动选定一个映射作为唯一答案。

### 5.4 无法建立映射

无法把 Values 合法映射到 Variables 时，状态为 `Cannot Evaluate`，并展示原始 Variables/Values 和失败原因。该状态不是 `Not Found`。

## 6. 值标准化

### 6.1 Character

- strict pass 保留内部空格、标点和大小写，做精确字符比较。
- 解析层只去除 Excel 单元格或解析过程中额外产生的非语义边界空白；该处理不等于宽松匹配。
- 字符串中的逗号是值的一部分。
- 不把字符型数字自动转为数值。
- 没有 strict exact 时运行 lenient pass：比较时忽略大小写，并忽略值的首尾空格。
- lenient pass 不忽略内部空格、标点、连字符或逗号。
- 任何需要 lenient comparison 才命中的 candidate 都属于黄色 review，并记录 expected value、actual value 和差异原因。

宽松比较不得静默升级为绿色 exact。

### 6.2 Numeric

- 使用十进制语义比较，避免二进制浮点格式造成假不匹配。
- `1`、`1.0` 和 `1.00` 视为相同数值。
- 科学计数法可规范化为相同数值。
- 非法数值文本导致该值映射无法评估，不退化为字符比较。

### 6.3 DTC 字符日期

当前 metadata 为 character 的 `--DTC` 按 P21 文本与当前数据文本精确匹配，例如：

```text
2026-03-01
2026-03-01T12:30
```

不同精度不自动补齐，不做时区换算。

### 6.4 null 和缺失值

P21 `Values` 中语义为 `null` 的值表示缺失，而不是字面字符串 `"null"`。

- numeric 变量：搜索 numeric missing。
- character 变量：搜索 character missing。
- metadata 无法判断类型时：同时尝试两种缺失语义，并把类型不确定性显示给用户。
- WHERE 表达式统一使用 `MISSING(variable)`，由 Viewer 的 WHERE 解析器按实际类型执行。

## 7. Candidate 搜索

### 7.1 Strict exact pass

使用除 `SEQ` 外的全部 `local_matchable` 条件进行严格搜索。所有条件按严格规则匹配时记录为 strict exact candidate。

cross-domain ignored fields 和 `SEQ` 不参与匹配，也不计入分母或 mismatch。

只要找到一个或多个 strict exact candidates，本 issue 匹配即结束，不再运行 lenient 或 partial pass。

### 7.2 Review pass

仅当 strict exact pass 没有结果时运行。令 `N` 为除 `SEQ` 和跨数据集引用外、当前 metadata 中存在的 local matchable variables 数量：

```text
required_matches = ceil(0.8 × N)
match_rate = matched_variables / N
candidate requires matched_variables >= required_matches
```

变量可通过 strict 或 lenient character comparison 计为 matched；只要使用了 lenient comparison，candidate 就保持黄色 review。未匹配变量可以多于一个，只要总匹配率达到阈值。

阈值示例：

| N | required_matches | 最多未匹配数 |
|---:|---:|---:|
| 1–4 | N | 0 |
| 5 | 4 | 1 |
| 10 | 8 | 2 |

如果当前 metadata 缺少任何 P21 local variable，则 issue 为 `Cannot Evaluate`，不通过缩小分母继续搜索。

如果 `N=0`，即 issue 只有 `SEQ`、跨数据集引用或其他不可用字段，同样标记为 `Cannot Evaluate`。

如果 issue 包含 `USUBJID`，review candidate 的 `USUBJID` 必须匹配；该约束优先于 80% 阈值。

每条 review candidate 必须记录：

- `_source_row`
- 已匹配变量及当前值
- 所有未匹配或 lenient-matched 变量
- P21 expected value
- 当前实际值
- Applied WHERE

### 7.3 SEQ

- `SEQ` 完全不参与第一版 record matching。
- `SEQ` 不进入 strict exact 条件、80% 分母、mismatch 列表或 WHERE。
- 只有 `SEQ`、没有其他可匹配本地变量的 issue 为 `Cannot Evaluate`。
- Values 对齐仍消费 `SEQ` 对应片段。

### 7.4 去重和多条结果

- candidate identity 为当前 snapshot 中的 `_source_row`。
- 同一记录由多个值映射或 review 路径找到时只显示一次，但保留全部命中原因。
- 匹配多条时全部展示，不自动排序出“最佳”记录，不自动选择一条。

## 8. Issue 状态与数量

匹配质量和候选数量分开表达。

### 8.1 状态优先级

1. 存在 strict exact candidate：`Matched – Exact`，淡绿色；不再计算 review candidates。
2. 无 strict exact，但存在 partial 或 lenient candidate：`Review – Partial/Lenient Match`，淡黄色。
3. 成功评估但没有 candidate：`Not Found in Current Data`，淡红色。
4. 缺少数据、变量、类型或值映射失败：`Cannot Evaluate`，淡灰色。

`Multiple Matches` 不是独立状态。是否多条通过 `Current Match Count` 表达。

### 8.2 计数

每条 issue 显示：

- `Exact Count`
- `Partial/Review Count`
- `Current Match Count`；单条 issue 只会有 strict exact 或 review candidates，不会同时存在两类

Rule ID 汇总显示：

- `P21 Issue Count`
- `Matched Issue Count`
- `Partial Review Issue Count`
- `Not Found Issue Count`
- `Cannot Evaluate Issue Count`
- `Current Exact Record Count`
- `Current Review Record Count`
- `Current Candidate Record Count`，按 `_source_row` 去重

## 9. SAS-style WHERE

### 9.1 生成规则

- 变量名使用 Viewer 当前 WHERE 语法支持的安全形式。
- character 值使用单引号，内部单引号按 Viewer 语法转义。
- numeric 值不加引号，使用规范化数值文本。
- 缺失值使用 `MISSING(variable)`。
- cross-domain variables 不进入 WHERE。
- `SEQ` variables 不进入 WHERE。

示例：

```sas
USUBJID = 'SUBJ-001' AND AETERM = 'Headache, severe' AND MISSING(AEENDY)
```

### 9.2 Expected WHERE

Expected WHERE 包含从 P21 issue 中成功映射的全部 eligible local variables，用于检查历史 issue 条件本身。`SEQ` 和 cross-domain variables 明确排除。

### 9.3 Applied WHERE

- strict exact candidate：与 Expected WHERE 相同。
- review candidate：省略所有未匹配变量；界面同时列出这些变量的 expected/actual value。
- lenient-matched character variable：candidate-specific Applied WHERE 使用当前数据中的 actual value 生成可执行的严格条件，同时保留 P21 expected value 和“忽略大小写/首尾空格后匹配”的原因。
- 每个 candidate 保存自己的 Applied WHERE。单条 issue 有多个 candidates 时，Issue Applied WHERE 使用这些 candidate clauses 的去重 OR，确保点击 issue 可以显示全部候选并检查实际执行条件。

### 9.4 Rule ID 汇总

Rule ID 的 `Filter All Matched Records` 使用去重 `_source_row` 集合物化结果，不生成超长 OR WHERE。界面应注明汇总结果来自 materialized candidate selection。

## 10. 示例

### 10.1 Exact match 与 null

```text
Dataset: AE
Variables: USUBJID, AETERM, AEENDY
Values: SUBJ-001, Headache, null
```

若当前变量类型分别为 character、character、numeric：

```sas
USUBJID = 'SUBJ-001' AND AETERM = 'Headache' AND MISSING(AEENDY)
```

### 10.2 字符值中有逗号

```text
Variables: USUBJID, AETERM, AESEV
Values: SUBJ-001, Headache, severe, MILD
```

不能机械拆成四个值。动态对齐应利用 `AETERM` 的当前 distinct values，将 `Headache, severe` 作为一个字符值。

### 10.3 SEQ 更新后仍为 strict exact

```text
P21: USUBJID='SUBJ-001', AESEQ=4, AETERM='Headache'
Current: USUBJID='SUBJ-001', AESEQ=5, AETERM='Headache'
```

`AESEQ` 第一版完全忽略。`USUBJID` 和 `AETERM` 都严格匹配，因此该记录是 strict exact candidate，使用淡绿色展示。

### 10.4 Cross-domain 引用

```text
Variables: USUBJID, RFICDTC, DM:RFICDTC
```

`DM:RFICDTC` 第一版忽略；当前 domain 中存在的 `USUBJID` 和 `RFICDTC` 仍可参与匹配。

## 11. 测试合同

至少覆盖：

- 表头不在固定行。
- 缺少或重复必需列。
- Count/Record 第一版范围过滤。
- exact character、numeric 和 missing 匹配。
- numeric 表示差异。
- DTC 字符精确匹配。
- 字符值中含一个或多个逗号。
- 多种 Values 映射保持歧义。
- cross-domain variable 忽略。
- SEQ 变化不影响 strict exact，也不进入 80% 分母或 WHERE。
- 80% 阈值使用 `ceil(0.8 × N)`，覆盖 `N=1–4`、`N=5` 和 `N=10`。
- USUBJID 存在时必须匹配。
- strict character match 与忽略大小写/首尾空格的 lenient match。
- lenient match 不忽略内部空格、标点、连字符或逗号。
- 找到 strict exact 后不再运行 review pass。
- 多 candidate 全部显示和 `_source_row` 去重。
- Not Found 与 Cannot Evaluate 不混淆。
- Expected WHERE 与 Applied WHERE 的差异。
- 单引号及特殊字符的 WHERE 转义。

测试数据必须为合成数据，不提交真实临床数据或真实 P21 report。
