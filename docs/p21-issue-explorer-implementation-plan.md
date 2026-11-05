# P21 Issue Explorer 具体施工计划

状态：P0–P5 已完成；P6 打包实现已完成，等待干净 Windows 验收

更新日期：2026-11-05

## 1. 施工目标

在不把 P21 业务逻辑写入 SAS Viewer 主体的前提下，实现一个可选 side-car：读取最新 P21 report、定位当前数据中的 strict exact/review candidates、生成可检查的 SAS-style WHERE，并通过 Viewer 新结果视图展示。

相关文档：

- [第一版需求](p21-issue-explorer-requirements.md)
- [架构设计](p21-issue-explorer-design.md)
- [匹配规范](p21-issue-matching-spec.md)

本计划同时记录施工阶段、已完成结果和后续验收。第 12 节记录用户已经确认的最终选择。

### 施工前强制阅读

开始编码的模型必须完整阅读上述四份文档，不能只读本施工计划：

1. 需求文档决定第一版范围和用户行为。
2. 匹配规范决定 Values、80% 阈值、SEQ、字符宽松匹配和 WHERE。
3. 架构设计决定 side-car、IPC、snapshot、结果视图和打包边界。
4. 本施工计划决定阶段、文件和验收顺序。

如文档出现冲突，以第 12 节用户确认结果为最高优先级，并先同步修正文档再编码。

## 2. 第一版不施工的内容

- `Count` 有值的汇总 issue。
- `Count` 和 `Record` 都为空的非行级 issue。
- 跨 domain 值联查。
- 重新执行 P21 validation rule。
- 自动判断 issue 已解决。
- Excel issue 导出。
- macOS/Linux 正式发布包；第一版以现有 Windows 打包流程为验收目标。

## 3. 现有代码接入点

施工前以当前代码为准再次核对，预计接入点如下：

- `clinical_data_viewer/ui/main_window.py`
  - 注册 `Tools → P21 Issue Explorer` action。
  - 调用独立 host adapter。
  - 不加入 report 解析或匹配代码。
- `clinical_data_viewer/domain.py`
  - 复用当前 dataset handle 和 metadata。
- `clinical_data_viewer/data_store.py`
  - 复用只读查询和 `_source_row` 能力。
- 当前新建筛选结果 tab 的通用流程
  - 抽成或复用 `P21 Result View Adapter`。
- `SASDataViewer.spec`
  - 基础版保持现有行为。
- `scripts/build_windows.ps1`
  - 增加可选 P21 扩展包构建路径。

## 4. 计划新增文件

```text
clinical_data_viewer/
└── integrations/p21/
    ├── __init__.py
    ├── host.py                  # side-car lifecycle / IPC server
    ├── protocol.py              # versioned message schema and framing
    ├── snapshot.py              # Dataset Snapshot v1 builder
    └── result_view_adapter.py   # Viewer result tab boundary

sidecars/p21-issue-explorer/
├── pyproject.toml
├── P21IssueExplorer.spec
├── src/p21_issue_explorer/
│   ├── __init__.py
│   ├── app.py
│   ├── models/
│   │   ├── issue.py
│   │   ├── candidate.py
│   │   └── status.py
│   ├── report/
│   │   ├── reader.py
│   │   └── header_detection.py
│   ├── matching/
│   │   ├── value_alignment.py
│   │   ├── normalization.py
│   │   ├── matcher.py
│   │   └── where_builder.py
│   ├── integration/
│   │   ├── local_socket_client.py
│   │   ├── protocol.py
│   │   └── snapshot_reader.py
│   └── ui/
│       ├── main_window.py
│       ├── import_page.py
│       ├── issue_table.py
│       ├── rule_summary.py
│       └── progress.py
└── tests/
    ├── fixtures/
    ├── unit/
    └── integration/

packaging/
└── p21-sidecar-manifest.json

tests/integration/p21/
├── test_host_protocol.py
├── test_snapshot_contract.py
└── test_result_view_adapter.py
```

实际施工时可以在不改变模块边界的前提下合并过小文件。

side-car 第一版计划使用 `PySide6` 构建 UI 和本地 socket，使用 `openpyxl` 以 read-only/data-only 方式读取 `.xlsx`；SQLite 使用 Python 标准库。依赖固定在 side-car 自己的 `pyproject.toml`，不无条件加入基础 Viewer 包。

## 5. 阶段 0：冻结合同和合成样本（已完成）

### 工作内容

- 用户完成第 12 节决策。
- 为 requirements、design、matching spec 增加一致的术语和状态枚举。
- 建立合成 `Issue Details` xlsx fixture。
- 建立对应的合成当前数据 fixture。
- fixture 覆盖 strict exact、80% review、lenient review、not found、cannot evaluate、null、逗号值、cross-domain、SEQ 忽略和多 candidate。

### 验证

- 逐条 fixture 写出预期 issue 状态、候选 `_source_row` 和 WHERE。
- 不包含真实 subject ID、真实临床值或真实 report。

### 完成门槛

- 第 12 节影响第一版行为的项目全部确认。
- fixture 预期结果可人工复核。

### 实施结果（2026-11-05）

- 已建立包含 9 条 eligible issues 的合成 `Issue Details` workbook，表头位于第 7 行。
- 已建立缺少 worksheet、缺少 required column、重复 required column 三个错误 workbook。
- 已建立 AE/LB 当前数据 CSV、变量类型 metadata 和 `expected_matches.json`。
- 合同覆盖 strict exact、80% review、lenient review、Not Found、Cannot Evaluate、numeric/character null、字符逗号、cross-domain、SEQ 忽略和多 candidate。
- 所有 identifier 和值均为合成内容，不包含真实临床数据。

## 6. 阶段 1：独立 side-car 骨架和 report reader（已完成）

### 工作内容

- 建立 side-car 独立 package、入口和测试配置。
- 实现 `Issue Details` worksheet 精确选择。
- 实现必需列驱动的表头发现，不依赖第 6 行。
- 实现 Count/Record 第一版范围过滤。
- 建立 typed issue model 和 import diagnostics。
- 建立最小 UI：选择 report、导入状态、domain 列表。

### 验证

- 单元测试：worksheet、表头、列缺失/重复、空值、范围过滤。
- side-car 可在不启动 SAS Viewer 时用 fixture 独立运行。

### 完成门槛

- report reader 对所有合成输入给出确定结果。
- 错误 report 不导致进程崩溃。

### 实施结果（2026-11-05）

- 已建立 `sidecars/p21-issue-explorer` 独立 package、console entry point 和独立依赖声明。
- report reader 使用 openpyxl read-only/data-only 模式，只选择精确 `Issue Details`。
- required header 在前 100 行按 trim/case-insensitive 列名发现，不依赖固定行号。
- 已实现 Count/Record 范围过滤、SHA-256 report fingerprint、typed issue model 和 import diagnostics。
- 独立 PySide6 UI 支持选择 report、后台导入、错误显示及 AE/LB domain selector，不引用 SAS Viewer package。
- 15 个 side-car tests、Ruff lint、side-car format check 和独立 wheel 安装 smoke test 已通过。

## 7. 阶段 2：Snapshot 合同和匹配核心（已完成）

### 工作内容

- 固化 `Dataset Snapshot v1` manifest schema。
- 实现 side-car read-only snapshot reader。
- 实现变量分类和数据类型解析。
- 实现 character/numeric/DTC/null 标准化。
- 实现 comma-safe Values 动态对齐。
- 实现 strict exact pass、`ceil(0.8 × N)` review pass、lenient character pass 和 `_source_row` 去重。
- 实现 issue/rule 计数。
- 实现 Expected WHERE 和 Applied WHERE builder。

### 验证

- matching spec 第 11 节全部形成参数化测试。
- 针对含逗号字符值进行 property-style 组合测试。
- WHERE 通过 Viewer 现有 parser/translator 的兼容测试。
- 同一输入重复运行结果一致。

### 完成门槛

- 匹配核心不依赖 Qt UI。
- 给定 report model + snapshot，输出可序列化且可完整解释。

### 实施结果（2026-11-05）

- 已固化 Dataset Snapshot v1 manifest，并实现 schema version、domain、变量类型、表/列、行数及 `_source_row` 唯一索引验证。
- side-car 通过 SQLite URI `mode=ro` 和 `query_only` 读取 snapshot，不写入 Viewer 内部存储。
- 已实现 metadata 驱动的 character/numeric/DTC/null 比较；numeric 使用十进制语义，character lenient 仅忽略大小写和首尾空格。
- Values 对齐保留原始逗号与空格，运行所有受类型约束的合法映射，再由完整 record candidate 结果消歧；歧义映射全部保留并按 `_source_row` 去重。
- 已实现 strict exact、`ceil(0.8 × N)` review、USUBJID anchor、SEQ/cross-domain 排除以及 Q15 exact 命中后停止。
- 已实现 Expected WHERE、candidate-specific Applied WHERE、单 issue 计数、Rule ID 汇总和可序列化解释模型。
- 40 个 side-car tests 已通过，覆盖冻结的 9 条端到端合同、阈值上下边界、多逗号、歧义、重复运行一致性和 Viewer WHERE parser/filter engine 兼容性。
- matching、integration 和 models 核心不引用 Qt 或 `clinical_data_viewer`；Viewer 兼容仅存在于测试层。

## 8. 阶段 3：独立 Explorer UI（已完成）

### 工作内容

- domain 和 Rule ID 选择器。
- Rule ID 汇总卡片。
- issue 明细表和 Current Match Count。
- 绿色 strict exact、黄色 partial/lenient review、红色 not found、灰色 cannot evaluate。
- candidate detail 显示 expected/actual/mismatched variable。
- Expected WHERE、Applied WHERE 的可见区域。
- 进度、取消、错误详情和重新导入。
- 使用 fake host/snapshot 开发，不依赖 Viewer 主窗口。

### 验证

- UI model 测试状态、计数、排序和筛选。
- 手工检查长 Message、长 Values、多个 candidate 和窗口缩放。
- 切换 domain/Rule ID 时，已取消任务不能回写旧结果。

### 完成门槛

- 使用合成 fixture 可完成完整浏览流程。
- UI 中每个数字都能追溯到明确计数定义。

### 实施结果（2026-11-05）

- 已实现 domain 与 Rule ID 选择、手动重新运行匹配、Rule ID 八项汇总数字和 `Filter All Matched Records` 操作。
- issue table 使用独立 `QAbstractTableModel`，支持排序、完整 tooltip、Exact/Review/Not Found/Cannot Evaluate 四种背景色及三类 candidate 数量。
- selected issue detail 展示 P21 原始信息、所有 candidate/mapping/variable 的 expected、actual、comparison result、reason，以及 Expected/Applied WHERE。
- 已定义可序列化语义明确的 `IssueOpenRequest` 和 materialized `RuleOpenRequest`，P3 只发出请求，不引用 Viewer UI 或内部数据库。
- Rule ID 匹配使用 `QRunnable + QThreadPool`；切换 domain/Rule、重新导入和取消会提升 generation，迟到结果不能覆盖当前页面。
- 无 snapshot 或 snapshot 失败时，当前 Rule ID 全部标记 `Cannot Evaluate`，不会误报为 `Not Found`。
- standalone/fake-host 模式支持重复 `--snapshot DOMAIN=PATH` 参数；窗口在 1420×920 可完整浏览，并在 900×600 紧凑尺寸下使用外层滚动保持可访问。
- 52 个 side-car tests 已通过，新增覆盖 UI model、排序、颜色、长文本、数字口径、candidate detail、standalone 参数、P4 request boundary、取消、generation stale-result protection 和紧凑窗口。

## 9. 阶段 4：Viewer Host 与结果视图集成

状态：已完成（2026-11-05）

### 工作内容

- 实现 QLocalServer/QLocalSocket length-prefixed JSON 协议。
- 实现随机 server name、session token 和 protocol handshake。
- Viewer 使用 QProcess 启动、监控、重启和关闭 side-car。
- 实现 Snapshot v1 builder 与生命周期清理。
- 增加 manifest 检测和 Tools 菜单隐藏逻辑。
- 实现单 issue 的新建/复用 P21 Result View。
- 把 Applied WHERE 放入 Viewer Where 框并执行。
- 实现 Rule ID 去重 `_source_row` 汇总视图。
- 保持原始数据 tab 不变。

### 验证

- 协议分帧、半包、多包、非法长度、错误 token 和版本不兼容测试。
- side-car 崩溃、Viewer 关闭和 snapshot 清理测试。
- single issue strict exact/review/not found 结果视图测试。
- Rule ID 汇总去重测试。
- side-car 不存在或损坏时菜单隐藏。

### 完成门槛

- P21 故障不影响 Viewer 打开和普通数据查看。
- 主程序中不存在 P21 匹配规则。

### 实施结果

- Viewer Host 已使用随机 server name、256-bit token、协议版本和 4-byte big-endian length-prefixed JSON；错误 token、错误版本、半包、多包和非法长度均在边界拒绝。
- `QProcess` 只管理 side-car 生命周期，`QLocalServer/QLocalSocket` 承担业务通信；side-car 退出或 Viewer 关闭时会回收 socket、server 和本会话 snapshots，重新点击菜单可重启。
- Viewer 已实现 Dataset Snapshot v1 builder，只导出请求变量与可用的 `STUDYID`/`USUBJID`，并由 side-car 只读打开；正常关闭 snapshot 时发送 `release_snapshot`。
- Tools 菜单只在 manifest、协议版本、executable 和 SHA-256 完整性全部有效时显示入口；基础版或损坏扩展保持隐藏。
- 单条 issue 与 Rule ID 汇总均新建或复用唯一的 `P21 Result View`；单条 WHERE 可见、可编辑且自动执行，Rule 汇总按 `_source_row` 去重并显示 materialized selection 及汇总数字。
- exact rows 使用淡绿色、review rows 使用淡黄色；Not Found 仍能打开空结果视图并保留 Expected WHERE，原始 dataset tab 的 WHERE、排序、列选择和位置不变。
- P21 匹配、状态判断和 WHERE 生成仍全部位于 side-car；Viewer 新增代码只包含通用协议、snapshot、进程和结果视图 adapter。
- 52 个 side-car tests 与 460 个 Viewer tests 已通过；另在沙箱外完成真实 `QLocalServer/QLocalSocket` handshake → snapshot → release 往返，以及 `QProcess` 启动 → 认证 → 关闭 side-car 的 smoke test。

## 10. 阶段 5：性能、缓存和稳定性

状态：已完成（2026-11-05）

### 工作内容

- Snapshot 只复制所需变量并建立查询索引。
- 按 report/domain/rule/snapshot/matcher version 缓存结果。
- domain 切换或源数据变化时使缓存失效。
- 大任务后台执行并支持取消。
- 增加受控日志和诊断信息，不记录完整患者值。
- 设置候选数量软上限提示，但不静默丢弃结果。

### 验证

- 使用合成大数据测试启动、快照和匹配时间。
- 重复选择同一 Rule ID 验证缓存命中。
- 更新 source fingerprint 后验证不会复用旧结果。
- 广泛 review 搜索验证 UI 仍可响应。

### 完成门槛

- 性能基线和测试规模记录在文档中。
- 所有耗时操作均不阻塞 Viewer 或 side-car UI。

### 实施结果

- Viewer 快照只复制本次需要的变量，并为 `_source_row` 和每个包含变量建立索引。
- Viewer 以有界 LRU 复用同一 source fingerprint 且变量集合已覆盖的空闲快照；源指纹变化、side-car 结束或 Viewer 退出时使旧快照失效并清理。
- side-car 使用 `report fingerprint + domain + rule id + source fingerprint + matcher version` 的有界线程安全 LRU 缓存 Rule 匹配结果；重新导入 report 时清空会话缓存。
- Snapshot 生成、Rule 匹配和 P21 Result View materialization 均在后台 worker 执行，并有 generation/cancellation 防护，过期结果不会覆盖当前页面。
- 大结果集通过临时 selection table 与 source 数据 JOIN，不受 SQLite 单条 `IN (...)` 参数数量限制。
- 候选数超过软阈值时显示警告但不截断；12,000 条 review candidates 的合成测试确认全部保留。
- 诊断日志只记录 domain、Rule ID、issue/candidate 计数和 cache hit，不记录 Values、WHERE 或患者数据。

### 性能基线

基线环境为 2026-11-05 当前 macOS 开发机，使用合成 SQLite 数据；这些数字用于防止明显回退，不代表 Windows 最终验收指标：

| 场景 | 数据规模 | 本次观测 | 自动测试上限 |
|---|---:|---:|---:|
| Snapshot 生成并 materialize P21 Result View | 25,000 rows、4 个业务变量、25,000 个候选 | 约 0.09 秒 | 各阶段小于 10 秒 |
| 无 exact 时的 broad review 搜索 | 12,000 rows、5 个匹配变量、12,000 个候选 | 约 0.18 秒 | 小于 15 秒 |

P5 自动验证结果：side-car 58/58 通过；Viewer P21 集成测试 13/13 通过，其中两项本地 socket/QProcess 测试在沙箱外补跑通过。P6 仍需在干净 Windows 环境重新测量并验收。

## 11. 阶段 6：双版本打包和 Windows 验收

状态：打包实现已完成（2026-11-05）；Windows 实机验收待执行

### 工作内容

- 建立 side-car PyInstaller spec。
- 扩展 Windows build script，生成基础版和 P21 扩展版。
- P21 版包含 manifest、side-car executable 和所需 runtime。
- 基础版不包含 side-car，Tools 菜单不显示入口。
- 在干净 Windows 环境做解压即用验收。

### 验证矩阵

| 场景 | 基础版 | P21 扩展版 |
|---|---:|---:|
| Viewer 正常启动 | 必须 | 必须 |
| P21 菜单 | 隐藏 | 显示 |
| 普通 SAS 浏览 | 必须 | 必须 |
| 导入 Issue Details | 不适用 | 必须 |
| 单 issue 结果视图 | 不适用 | 必须 |
| Rule ID 汇总结果 | 不适用 | 必须 |
| side-car 异常后 Viewer 可用 | 不适用 | 必须 |
| 无外部网络仍可运行 | 必须 | 必须 |

### 完成门槛

- 两个 ZIP 均可在干净 Windows 主机运行。
- 扩展版 side-car 的启动、退出、临时文件清理通过。
- 回归测试、静态检查和打包 smoke test 全部通过。

### 已实施

- 新增独立 `P21IssueExplorer.spec` 和冻结入口，side-car 继续保持独立 onedir runtime。
- Windows build script 默认只生成基础 ZIP；显式 `-IncludeP21` 时先保留基础 ZIP，再构建、smoke-test、注入和验证 P21 side-car，生成第二个 P21 ZIP。
- P21 side-car 位于 Viewer `_internal/p21-sidecar`；manifest 位于 Viewer `_internal`，包含协议版本、相对 executable 路径、空参数和 executable SHA256。
- 新增跨平台 staging/verification helper：验证基础版不含 P21 文件，并验证 P21 onedir 完整性、路径边界和 SHA256；篡改 executable 的测试会失败。
- macOS 开发机已完成 side-car PyInstaller onedir smoke build、无界面 executable smoke test，以及 packaged executable 的真实 QLocalSocket 握手/认证/退出测试。
- Windows 验收清单已增加基础版/P21 版、无 Python/离线、完整 P21 流程、崩溃隔离和临时文件清理项目。
- 当前自动回归：Viewer 467 项通过（沙箱内 2 项 local-socket 跳过后已在沙箱外 2/2 补跑通过）；side-car 59/59 通过；P21 staging/manifest/hash 合同测试 3/3 包含在 Viewer 测试中。

### 尚未完成

- 当前环境不能交叉生成或验收 Windows EXE，因此尚未生成最终 Windows ZIP，也未在干净 Windows 10/11 x64 主机执行第 28–32 项。完成这些实机检查前，P6 不能标记为完全验收通过。

## 12. 用户确认结果

以下选择均已由用户确认。施工时以已勾选项为准；未勾选项仅保留决策背景。

### Q1. Partial match 最低条件

- [ ] A（推荐）：恰好一个 local variable 不匹配，并且至少两个 local variables 匹配。
- [ ] B：恰好一个 local variable 不匹配，只要求至少一个 local variable 匹配。
- [x] C：至少 80% 的 eligible local variables 匹配；`required_matches = ceil(0.8 × N)`。`SEQ` 和跨数据集引用不计入 `N`。

说明：`N=1–4` 时必须全部匹配，`N=5` 时至少匹配 4 个，`N=10` 时至少匹配 8 个。当前数据缺少 local variable 时按 Q7 处理，不通过缩小 `N` 继续匹配。

### Q2. 含 USUBJID 时的 partial match

- [x] A（推荐）：P21 issue 含 `USUBJID` 时，review candidate 必须匹配 `USUBJID`。
- [ ] B：允许 `USUBJID` 成为唯一不匹配变量。
- [ ] C：按 domain 设置：__________。

说明：A 可避免把其他 subject 的记录误列为候选。

### Q3. SEQ 的处理

- [ ] A（推荐）：SEQ 是弱条件，可参与 exact，也可以是 partial 的唯一 mismatch；不能单独定位。
- [x] B：SEQ 完全不参与第一版匹配，不进入 strict exact、80% 分母、mismatch 或 WHERE；Values 对齐仍消费其对应值，避免后续值错位。
- [ ] C：SEQ 与普通变量同等处理。

### Q4. Current Match Count 口径

- [x] A（推荐）：显示 strict exact + review 去重总数，并同时显示 Exact Count 和 Review Count。
- [ ] B：只把 exact 算入 Current Match Count，partial 另列。
- [ ] C：其他口径：__________。

### Q5. Filter All Matched Records 是否包含 partial

- [x] A（推荐）：包含 strict exact 和 review candidates，去重展示；review 行保持黄色。
- [ ] B：只包含 exact；partial 只能逐条 issue 查看。
- [ ] C：增加 UI 开关，默认：__________。

### Q6. 同一 issue 同时存在 exact 和 partial 时的状态

- [ ] A（推荐）：issue 行显示绿色 `Matched – Exact`；展开后 exact records 绿色、partial records 黄色，并显示各自数量。
- [ ] B：issue 行显示黄色，提醒仍存在 partial candidates。
- [x] C：只要有 strict exact 就不再搜索 partial 或 lenient candidates；issue 行显示绿色，只展示 strict exact records。

### Q7. P21 local variable 在当前数据中不存在

- [x] A（推荐）：标记灰色 `Cannot Evaluate`，不当作 partial 或 not found。
- [ ] B：忽略缺失变量，使用其余变量搜索并标黄。
- [ ] C：直接标记 `Not Found in Current Data`。

### Q8. 没有稳定 anchor 的宽泛 review 搜索

- [x] A（推荐）：仍展示全部 review candidates 并标记 `Broad Review`；候选很多时提示性能和误报风险，不静默截断。
- [ ] B：没有 `USUBJID` 或其他稳定 anchor 时不运行 partial。
- [ ] C：候选超过 ________ 条时要求用户手动继续。

### Q9. Rule ID 汇总视图的 Where 框

- [x] A（推荐）：用去重 `_source_row` 集合物化结果；Where 区域显示“Materialized P21 candidate selection”，不生成超长 OR。单 issue 仍显示可执行 WHERE。
- [ ] B：尝试生成所有 issue 的 OR WHERE，即使表达式很长。
- [ ] C：Rule ID 汇总视图隐藏 Where 区域。

### Q10. Windows 制品名称

- [x] A（推荐）：`SASDataViewer-Windows-x64.zip` 与 `SASDataViewer-P21-Windows-x64.zip`。
- [ ] B：使用版本号名称：__________。
- [ ] C：其他名称：__________。

### Q11. P21 Result View 的复用范围

- [x] A（推荐）：每个 Explorer 会话复用一个结果 tab；点击新 issue 时更新该 tab，domain 变化时仍复用并清空旧状态。
- [ ] B：每个 domain 一个结果 tab。
- [ ] C：每次点击都新开一个结果 tab。

### Q12. 第一版是否接受 Viewer 与 side-car 各带一份 Qt runtime

- [x] A（推荐）：接受，优先保证隔离和打包稳定；后续再优化体积。
- [ ] B：第一版就必须共享 runtime，优先减小安装包。

### Q13. Values 存在多个合法映射时

- [x] A（推荐）：运行所有合法映射、合并去重候选，并标记 `Ambiguous Value Mapping`。
- [ ] B：停止该 issue，标记 `Cannot Evaluate`，不搜索候选。
- [ ] C：按候选数最少的映射自动选择一个。

### Q14. Character 比较规则

- [ ] A（推荐）：第一版区分大小写并保留内部空格，只清理解析产生的非语义首尾空白。
- [ ] B：第一版字符比较不区分大小写。
- [x] C：先运行严格字符比较；没有 strict exact 时再忽略大小写及首尾空格搜索。宽松比较不忽略内部空格、标点、连字符或逗号；命中标黄并显示差异。

### Q15. Exact 存在时是否继续搜索 partial

- [ ] A（推荐）：继续搜索 partial，满足“宁可多报”的目标；UI 分开显示 exact/partial。
- [x] B：找到 strict exact 后停止，不再搜索 partial 或 lenient candidates。
- [ ] C：提供用户开关，默认：__________。

## 13. 风险与控制

| 风险 | 控制方式 |
|---|---|
| Values 中逗号导致错位 | 类型约束动态对齐；歧义不自动裁决 |
| review 候选过多 | 稳定 anchor、80% 阈值、后台任务、Broad Review 提示 |
| Viewer 与 side-car 强耦合 | IPC 合同 + Snapshot v1 + host adapter |
| Windows GUI stdio 不可靠 | QProcess 只管理进程，业务通信使用 QLocalSocket |
| 当前数据更新后缓存过期 | source fingerprint 参与缓存键 |
| side-car 故障拖垮 Viewer | 独立进程、超时、错误隔离和资源清理 |
| 真实临床数据进入仓库或日志 | 合成 fixture、日志脱敏、快照会话清理 |
| 汇总 WHERE 过长 | `_source_row` 去重集合物化结果 |

## 14. 回退策略

- 基础版构建路径始终保留，不依赖 side-car。
- P21 菜单受 manifest 控制；扩展组件异常时可隐藏入口，不改变核心 Viewer。
- host adapter 改动保持小而独立，可在不回退 Viewer 其他功能的情况下禁用。
- Snapshot schema 和 IPC protocol 均有版本号；不兼容时明确拒绝连接，不静默误读。

## 15. 建议施工顺序

建议先完成第 12 节确认，然后严格按阶段 0 → 6 施工。阶段 1–3 可主要在 side-car 目录独立完成；直到匹配合同和独立 UI 稳定后，再进入 Viewer 集成和 Windows 打包，从而把主程序回归风险控制在后半段。
