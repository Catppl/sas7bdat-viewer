# P21 Issue Explorer 第一版需求

状态：第一版需求与用户决策已确认；P0–P4 已实现，P5–P6 待施工

更新日期：2026-11-05

## 1. 文档目的

P21 Issue Explorer 用于读取 Pinnacle 21（P21）验证报告，在每天更新的当前 SAS 数据中定位与历史 issue 对应的候选记录。

本文件只定义第一版用户行为、范围和验收标准。技术实现分别见：

- [P21 Issue Explorer 架构设计](p21-issue-explorer-design.md)
- [P21 Issue 匹配规范](p21-issue-matching-spec.md)
- [P21 Issue Explorer 施工计划](p21-issue-explorer-implementation-plan.md)

## 2. 产品定位

- 功能名称固定为 `P21 Issue Explorer`。
- 作为 SAS Viewer 的可选 lazy side-car 提供。
- 菜单入口为 `Tools → P21 Issue Explorer`。
- 未打包 side-car 的基础版隐藏该菜单项。
- side-car 未启动时不增加 SAS Viewer 的常驻资源占用。
- P21 功能故障不得影响 SAS Viewer 核心数据查看功能。

## 3. 第一版范围

第一版只处理同时满足以下条件的 P21 issue：

- 来源 worksheet 名称完全匹配 `Issue Details`。
- `Count` 为空。
- `Record` 有值。

第一版明确忽略：

- `Count` 有值的汇总类 issue。
- `Count` 和 `Record` 都为空的非行级 issue。
- `Issue Details` 以外的 worksheets。
- `DATASET:VARIABLE` 格式的跨数据集引用字段，例如 `DM:RFICDTC`。
- 自动判断 issue 是否已解决。
- 重新运行 P21 验证规则。
- Excel issue 导出；该功能保留为后续版本需求。

## 4. P21 report 导入

### 4.1 每次打开重新导入

用户每次打开 P21 Issue Explorer 时，都需要选择本次要分析的最新 P21 report。

第一版不自动复用上一次导入的文件，避免误用过期 issue。

### 4.2 Worksheet 和表头

- 只读取 `Issue Details` worksheet。
- 缺少该 worksheet 时显示明确错误，不从其他 worksheet 推断。
- 程序根据必需列名发现表头，不依赖表头固定在 Excel 第 6 行。
- 必需列缺失或重复时停止导入，并列出具体列名。

必需字段：

- `Dataset`
- `Record`
- `Count`
- `Variables`
- `Values`
- `Rule ID`
- `Message`
- `Impact`
- `Type`

## 5. 用户操作流程

1. 用户通过 SAS Viewer 打开 P21 Issue Explorer。
2. 用户导入最新 P21 report。
3. 用户选择 domain/dataset。
4. 用户选择 `Rule ID`。
5. 界面显示该 domain 和 Rule ID 的汇总数量及 issue 明细。
6. 用户可以点击单条 issue 查看候选 records。
7. 用户可以点击 `Filter All Matched Records` 查看当前 Rule ID 的全部候选 records。

如果 P21 report 中存在某个 domain，但当前 SAS Viewer 工作区没有对应数据集，界面显示当前数据不可用，不把它解释为 issue 已解决。

## 6. Rule ID 汇总

选择 domain 和 Rule ID 后，至少显示：

- `P21 Issue Count`：第一版范围内的 P21 issue 明细行数。
- `Current Match Count`：当前数据中 strict exact 和黄色 review candidates 合并去重后的 record 数量。
- `Matched Issue Count`：至少存在一个 strict exact candidate 的 issue 数量。
- `Partial Review Issue Count`：没有 strict exact、但存在 partial 或 lenient review candidate 的 issue 数量。
- `Not Found Issue Count`：没有 strict exact 或黄色 review candidate 的 issue 数量。
- `Cannot Evaluate Issue Count`：由于当前数据、变量、类型或值映射不足而无法评估的 issue 数量。
- `Current Exact Record Count`：去重后的 strict exact records 数量。
- `Current Review Record Count`：去重后的黄色 review records 数量，包括 partial 和 lenient character matches。

`Current Match Count` 不得命名为 `Count`，避免与 P21 report 原始 `Count` 字段混淆。

同一个当前 record 被同一 Rule ID 下多个 issue 匹配时，在 Rule ID 汇总中只计算一次。

## 7. Issue 明细列表

列表至少展示：

- 当前显示状态
- `Rule ID`
- `Record`，仅作为 P21 历史参考
- `Variables`
- `Values`
- `Message`
- `Impact`
- `Type`
- `Exact Match Count`
- `Partial/Review Match Count`
- `Current Match Count`

### 7.1 状态与颜色

Issue 行状态按以下优先级确定：

| 条件 | 状态 | 背景色 |
|---|---|---|
| 至少有一个 strict exact candidate | `Matched – Exact` | 淡绿色 |
| 没有 strict exact，但至少有一个 partial 或 lenient candidate | `Review – Partial/Lenient Match` | 淡黄色 |
| 没有任何候选 record | `Not Found in Current Data` | 淡红色 |
| 无法解析或无法评估 | `Cannot Evaluate` | 中性灰色 |

候选数量与匹配质量是两个独立维度。多条候选通过数量显示，不再把 `Multiple Matches` 作为与 Exact/Partial 并列的状态。

### 7.2 召回优先

第一版遵循召回优先原则：宁可保留需要人工判断的候选记录，也不要因为一个字段变化而漏掉可能对应的记录。

- Strict exact candidate：除 `SEQ` 外，所有可评估本地变量都按严格规则匹配。找到 strict exact 后立即停止，不再搜索 partial 或 lenient candidates。
- Review candidate：仅在没有 strict exact 时搜索；至少 80% 的可评估本地变量匹配，所需匹配数为 `ceil(0.8 × N)`。
- `N` 只计算当前数据中存在的本地可匹配变量；`SEQ` 和跨数据集引用不进入分母。当前数据中缺少本地变量时，该 issue 为 `Cannot Evaluate`。
- P21 issue 含 `USUBJID` 时，review candidate 必须匹配 `USUBJID`。
- 字符宽松匹配可以计为已匹配，但 candidate 必须标黄。
- Review candidate 必须列出所有不匹配或宽松匹配变量、P21 值和当前值。
- 多条候选全部展示，不自动选择第一条或“最相似”的一条。

例如 `N=4` 时需要匹配 4 个变量，`N=5` 时需要匹配 4 个，`N=10` 时需要匹配 8 个。完整算法见匹配规范。

## 8. 类型和特殊值

- `Record` 不作为当前数据主键，也不假设当前行号与 P21 一致。
- `--SEQ` 完全不参与第一版匹配，不进入匹配条件、80% 分母或 WHERE；解析 Values 时仍须消费其对应值，避免后续值错位。
- `USUBJID` 或其他稳定信息用于缩小候选范围。
- `DATASET:VARIABLE` 跨数据集字段第一版忽略，不写入匹配条件。
- `null` 表示缺失值，不作为普通字符串 `"null"` 搜索。
- 变量类型以当前数据集 metadata 为准。
- numeric 变量按数值语义匹配，例如 `1` 与 `1.0` 相同。
- character 变量先严格匹配；没有 strict exact 时，再进行忽略大小写及首尾空格的宽松匹配。
- 字符宽松匹配不忽略内部空格、标点、连字符或逗号，命中时标黄并显示差异。
- `*DTC` 变量按字符型 ISO 日期/时间文本匹配，并保留原始精度。

完整解析和标准化规则见匹配规范。

## 9. 点击单条 issue

点击 issue 后：

1. P21 Issue Explorer 生成 Expected WHERE 和 Applied WHERE。
2. SAS Viewer 基于对应 domain 新开或复用 `P21 Result View`。
3. Applied WHERE 写入新视图的 Where 搜索框。
4. SAS Viewer 使用该 Applied WHERE 执行筛选。
5. 所有候选 records 在结果视图中展示。

Expected WHERE 表示 P21 历史值形成的完整期望条件。Applied WHERE 表示实际用于显示候选 records 的条件；黄色 review match 时二者可能不同。

Where 搜索框中的条件必须可见、可编辑。用户可以修改并重新执行，以判断无结果是条件问题还是数据已变化。

## 10. P21 Result View

- 不覆盖用户原始 dataset view。
- 不修改原始 view 的 WHERE、列选择、排序或浏览状态。
- 使用当前数据，只读，不修改 SAS 原始文件。
- 同一 P21 Explorer 会话复用一个结果视图；点击新 issue 时更新该 tab，domain 变化时复用并清空旧状态。
- 即使没有候选 records，也可以打开空结果视图并显示 WHERE。
- Strict exact records 使用淡绿色提示；partial 或 lenient review records 使用淡黄色提示。
- Not Found 只在 issue 列表显示淡红色，因为结果视图中没有对应 record。

## 11. Filter All Matched Records

该操作范围为当前选择的：

```text
Domain + Rule ID
```

- 合并该 Rule ID 下所有 strict exact 和黄色 review candidates。
- 根据当前数据的 source-row identity 去重。
- 新开或复用 P21 Result View。
- 第一版不要求把全部 issue 拼接成一个巨大的 SAS WHERE；可以使用 materialized result selection。
- Review records 仍保持淡黄色提示，供用户人工判断。

Rule ID 汇总筛选包含 strict exact 和黄色 review candidates，并按当前 record 去重。

## 12. Not Found 的含义

`Not Found in Current Data` 只表示当前数据中没有找到候选 record。

它不表示：

- issue 已解决。
- 原始数据已正确修复。
- 重新运行 P21 后该规则不会再触发。

确认 issue 是否仍存在，必须使用当前数据重新运行对应的 P21 验证规则。

## 13. 可选打包

需要提供两种 Windows 安装包：

### 13.1 基础版

- 不包含 P21 side-car 和其专用依赖。
- Tools 菜单隐藏 `P21 Issue Explorer`。
- SAS Viewer 原有功能和启动流程不受影响。

### 13.2 P21 扩展版

- 包含独立 P21 side-car 及其依赖。
- Tools 菜单显示并启用 `P21 Issue Explorer`。
- SAS Viewer 启动时不启动 side-car。
- 用户点击菜单后才 lazy 启动 side-car。

两种安装包使用相同 SAS Viewer 核心版本基线。

## 14. 第一版验收标准

- 只读取 `Issue Details`，并验证必需列。
- 忽略 `Count` 有值及 `Record`/`Count` 都为空的 issue。
- 每次打开要求导入 report。
- 支持 domain 和 Rule ID 选择。
- 汇总数量定义明确且可复核。
- 不使用 P21 `Record` 作为当前数据主键。
- 正确处理字符、数值、`null` 和 `*DTC`。
- 不因 `Values` 中含逗号而错误拆分字符值。
- 跨数据集字段第一版被忽略。
- Strict Exact、Review、Not Found 和 Cannot Evaluate 可区分。
- 多候选全部展示，不自动选择。
- 点击 issue 后打开或复用 P21 Result View。
- Applied WHERE 可见、可编辑，并能由现有 Where parser 执行。
- 原始 dataset view 不被修改。
- Rule ID 全部结果按当前 record 去重。
- side-car 缺失、崩溃或协议不兼容不影响 SAS Viewer 核心功能。
- 基础版隐藏菜单；扩展版 lazy 启动 side-car。
- 第一版不实现 Excel 导出或 P21 规则重跑。

## 15. 后续版本：Excel issue 导出

该功能已记录但不属于第一版。

计划 workbook：

```text
Issue Summary
Issue Details
<Rule ID 1>
<Rule ID 2>
...
Export Info
```

- 每个 Rule ID 的匹配 records 单独放在一张 worksheet。
- worksheet 默认使用 Rule ID 命名。
- 同一 Rule ID 下相同当前 record 去重。
- Issue Details 保留 Strict Exact/Review/Not Found 状态和颜色。
- 导出结果只用于 review，不替代 P21 验证。

## 16. 用户确认状态

用户选择已记录在 [施工计划的确认结果](p21-issue-explorer-implementation-plan.md#12-用户确认结果)。所有问题均已单选确认；P0–P4 已完成，性能/缓存增强和打包尚未施工。
