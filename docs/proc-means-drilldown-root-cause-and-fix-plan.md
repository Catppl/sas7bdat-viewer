# PROC MEANS Builder Drill-down 失效：根因与修复施工说明

审查日期：2026-08-29  
审查基线：`c77d082` (`fix issue`)

## 1. 结论

PROC MEANS Builder 生成的 `PROC MEANS Result` 中：

- 右键 `Drill Down to Source Rows` 可点击；
- 双击统计单元格也会触发；
- 但无法打开新的 `Query: ...` Tab。

根因已经重放并确认：不是统计计算、Filter、metadata 或右键菜单状态问题，而是 Controller 迁移后遗漏了 MainWindow 的共享 Tab 标题 helper。

当 Drill-down 后台查询已经成功建立临时 Query handle 时，`ProcMeansController` 的完成回调会调用：

```python
title = self._host.unique_analysis_tab_title(base_title)
```

但 `MainWindow.unique_analysis_tab_title()` 当前又转调一个不存在的方法：

```python
def unique_analysis_tab_title(self, base: str) -> str:
    return self._unique_dataset_tab_title(base)
```

实际异常为：

```text
AttributeError: 'MainWindow' object has no attribute '_unique_dataset_tab_title'
```

因此 Query 数据已经在后台生成，但完成回调异常，无法调用 `show_proc_means_query_result()` 创建新 Tab。错误会进入通用 worker error callback；用户体验上表现为右键和双击都“没有反应”。

## 2. 为什么右键菜单仍然可用

右键菜单是否可用只由 PROC MEANS Result metadata 决定：

```text
metadata.proc_means_statistic_keys
```

当前 Result Writer 仍正确写入：

- `proc_means_analysis_column`
- `proc_means_statistic_keys`

所以右键菜单可用，双击也会发出 signal。这证明故障发生在 signal 之后的完成回调，不在 UI 识别统计列之前。

## 3. 回归来源

`_unique_dataset_tab_title()` 原本位于 `MainWindow`，负责：

```text
Query: Mean: 81.5
Query: Mean: 81.5 (2)
Query: Mean: 81.5 (3)
```

这样的 Tab 名去重。

在 commit `0a58226`（per-analysis controller migration）中，旧 Categorical workflow 连同该 private helper 一起从 `MainWindow` 移除；随后新 host wrapper `unique_analysis_tab_title()` 被保留，但其调用目标没有恢复。

这个遗漏不只影响 PROC MEANS。下列现有路径也依赖同一 host API：

- PROC MEANS Drill-down Query Tab
- Rule-based Drill-down / Long Result
- Categorical Drill-down / Long Result
- AE Table Drill-down / Long Result

此外，`Merge Datasets` 仍直接调用 `_unique_dataset_tab_title()`，所以同一个缺失 helper 也可能影响 Merge Result 创建。

## 4. 本次修复范围

本次只恢复共享标题去重能力，并为 PROC MEANS Builder Drill-down 补齐端到端回归测试。

不修改：

- `ProcMeansEngine` 统计逻辑
- `ProcMeansConfig` 与 JSON contract
- Filter parser / Filter AST
- Drill-down WHERE 业务规则
- PROC MEANS SAS / R code generator
- Builder Filter UI
- 其他 Analysis 模块的业务计算

## 5. 施工步骤

### Phase 1：恢复 MainWindow 的共享标题 helper

涉及：

- `clinical_data_viewer/ui/main_window.py`

在 `MainWindow` 中恢复私有方法：

```python
def _unique_dataset_tab_title(self, base: str) -> str:
    existing = {self.tabs.tabText(index) for index in range(self.tabs.count())}
    if base not in existing:
        return base
    suffix = 2
    while f"{base} ({suffix})" in existing:
        suffix += 1
    return f"{base} ({suffix})"
```

保留现有 public host wrapper：

```python
def unique_analysis_tab_title(self, base: str) -> str:
    return self._unique_dataset_tab_title(base)
```

理由：

- 这是最小修复；
- 已有 Merge 代码仍直接依赖私有 helper；
- AnalysisController 的 host contract 无需改变；
- 不会改变任何结果数据或 filter 语义。

### Phase 2：补强 PROC MEANS Drill-down 的错误提示

涉及：

- `clinical_data_viewer/controllers/analysis/proc_means.py`

当前以下分支会静默 `return`：

```python
if context is None or analysis_column is None or statistic_key is None:
    return
```

改为向用户显示明确消息，但不尝试猜测或重建 context：

```text
PROC MEANS Drill-down is unavailable for this result tab.
Please re-run PROC MEANS Builder and try again.
```

边界：

- `context is None` 时不能反向从结果 SQLite 或 JSON 猜 source handle；原 source 的临时生命周期、排序和 filter snapshot 都需要原始运行 context。
- `analysis_column` 或 `statistic_key` 缺失时也不要执行 query，避免错误地把非统计列当作统计单元格。
- 正常 Builder Result 不应显示该消息；这是异常状态的可诊断保护。

### Phase 3：新增端到端 UI / Controller 回归测试

涉及：

- `tests/test_proc_means_builder.py`
- `tests/test_ui_smoke.py` 或新增小型 `tests/test_proc_means_drilldown_ui.py`
- 如有必要，`tests/test_analysis_controller.py`

#### 必测场景 A：正常 Builder Result 的双击

1. 建立真实 source SQLite fixture。
2. 使用 `ProcMeansEngine` 生成真实 `kind="proc_means"` Result handle。
3. 通过 `MainWindow._make_dataset_tab()` 创建 Result Tab，确保使用生产 signal wiring。
4. 登记对应 `ProcMeansResultContext`，模拟 Builder 正常完成后的状态。
5. 载入结果页面后双击 `MEAN` 单元格。
6. 同步执行测试 worker，断言：
   - 调用 `show_proc_means_query_result()`；
   - title 为 `Query: Mean: <display value>`；
   - WHERE 含 Builder Filter、完整 BY/CLASS group、`not missing(analysis variable)`；
   - 新 Query handle 的行数与该统计组对应的 source rows 一致。

#### 必测场景 B：右键 Drill-down

1. 对同一个 `MEAN` 单元格触发 `CopyTableView.drilldown_requested`。
2. 验证和双击走到同一 Query Tab 构建路径。
3. 确认统计列 action enabled；非统计列 action disabled。

#### 必测场景 C：重复标题

1. 已存在 `Query: Mean: 1.55` Tab。
2. 再次 drill-down。
3. 结果标题必须为 `Query: Mean: 1.55 (2)`。

#### 必测场景 D：异常 context 不再静默

1. 创建带 PROC MEANS statistic metadata、但未注册 runtime context 的 Result Tab。
2. 触发 Drill-down。
3. 断言显示明确 information/warning；不提交 worker；不创建 Query Tab。

#### 必测场景 E：共享 helper 回归

1. `MainWindow.unique_analysis_tab_title("Result")` 在空 Tab 集合返回 `Result`。
2. 已存在 `Result` 时返回 `Result (2)`。
3. 已存在 `Result`、`Result (2)` 时返回 `Result (3)`。
4. Merge Result 的 title 创建路径继续可用，防止同一 helper 再次被删除而未发现。

## 6. 验收与回归命令

先运行定向测试：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest \
  tests.test_proc_means_builder \
  tests.test_proc_means_builder_filter \
  tests.test_analysis_controller \
  tests.test_ui_smoke -v
```

再运行所有可能受共享 helper 影响的模块：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest \
  tests.test_merge_datasets \
  tests.test_rule_based \
  tests.test_categorical_engine \
  tests.test_ae_table -v
```

最后运行完整套件：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

人工验收：

1. 从真实 SAS/XPT source 打开 PROC MEANS Builder。
2. 运行一个含至少一个 BY 或 CLASS 分组、一个 Analysis Variable、`MEAN` 的 Result。
3. 双击 `MEAN`，确认出现新的 `Query: Mean: ...` Tab。
4. 右键同一单元格选择 `Drill Down to Source Rows`，确认也能出现 Query Tab。
5. 在 Query Tab 的 WHERE 框确认其包含原 Builder Filter、group 条件和 `not missing(<analysis variable>)`。
6. 重复 Drill-down，确认 tab title 自动加 `(2)`，不覆盖原 Query Tab。

## 7. 完成边界

本施工完成后，PROC MEANS Builder 的 Drill-down 应恢复到：

```text
PROC MEANS Result statistic cell
        ↓ double-click / right-click
runtime result context
        ↓
background source-row query
        ↓
unique Query tab title
        ↓
read-only Query Tab + copyable WHERE
```

本次不为 Simple PROC MEANS 侧边栏增加 Drill-down；那是独立功能扩展，避免混入本次回归修复。

## 8. 本次实施记录

已按上述范围完成：

- `MainWindow` 恢复共享 `_unique_dataset_tab_title()`，保留现有
  `unique_analysis_tab_title()` host API。
- `ProcMeansController` 在结果 context、analysis column 或 statistic key
  缺失时显示明确的不可用提示，不再静默返回。
- `tests/test_ui_smoke.py` 增加真实 `ProcMeansEngine` 结果、生产 `MainWindow`
  signal wiring、真实页面加载、双击/右键入口、WHERE、Query 行数和重复标题的
  回归测试。
- `tests/test_analysis_controller.py` 增加 context 缺失时的提示回归测试。

验证结果：

- PROC MEANS / Filter / Controller / UI smoke 定向套件：39 tests passed
- 全量测试：240 tests passed
- `compileall` 与 `git diff --check` 通过
