# PROC MEANS Builder Filter 更新审查与后续事项

审查日期：2026-08-28
审查基线：`6989c5e` (`update filter for proc means`)

## 审查结论

本次更新的核心行为已经正确落地：

- Filter 已改为 Builder 内可编辑的单行 SAS-like WHERE 输入框。
- 第一次绑定 source 时会继承该 Dataset Tab 当前的 WHERE。
- Builder Filter 后续不会跟随 Dataset Tab WHERE 实时变化。
- Run、SAS Code Generator 和 R Code Generator 共用 Builder Filter 构建 `ProcMeansConfig`。
- 原来的 `Apply the current dataset filter to PROC MEANS?` 确认流程已从运行路径移除。
- 无效 WHERE 继续通过现有 `FilterEngine` 和 `QMessageBox` 报错。
- Clear 会清空 Filter。

相关回归测试与完整测试套件均通过。以下记录审查发现、实施方案及本轮处理状态，便于后续追踪。

## 本轮施工状态

- Phase 1（Reload 生命周期、Builder metadata 同步、失效变量裁剪）：已完成。
- Phase 2（移除 PROC MEANS 旧 Filter helper、Run/SAS/R Filter snapshot 测试）：已完成。
- Phase 3（`.DS_Store` 仓库卫生）：已完成工作区准备，待单独 maintenance commit；未改写已推送历史。
- 统计引擎、JSON contract、Filter parser、SAS/R generator 业务逻辑：未修改。

## 1.（已修复）Reload 后 Builder metadata 不会被主动刷新

优先级：高
类型：功能遗漏 / Reload 生命周期

### 现状

`ProcMeansBuilder.set_dataset()` 已经包含“同一 source 获得新 metadata 时保留 Builder Filter 和变量选择”的分支，但 Dataset Tab 的 Reload 完成后只执行了 `tab.replace_handle(...)` 和主 Variables 面板刷新，没有通知已绑定的 PROC MEANS Builder。

因此，Builder 已打开并绑定 ADLB 时，如果用户 Reload ADLB：

- 实际 Run 会使用 Reload 后的新 `tab.handle.metadata` 进行 Filter 编译和 Config validation；
- 但 Builder 中 Analysis / BY / CLASS 的 completer 与变量清单仍可能保留 Reload 前的 metadata；
- 新增变量不会出现在 Builder 中；
- 已删除变量仍可能留在已选列表中，直到 Run 时才报 validation error。

### 建议修复

建立通用的 analysis source reload 通知，例如：

```text
Dataset reload completed
        ↓
AnalysisController.source_reloaded(tab)
        ↓
如果 tab 是 PROC MEANS 固定 source
        ↓
ProcMeansController._set_proc_means_dataset(tab)
```

刷新时必须：

- 保留 Builder 自己的 Filter 内容，不重新继承 Dataset Tab 当前 WHERE；
- 保留仍然存在的 Analysis / BY / CLASS 变量；
- 自动移除已经不存在的变量；
- 更新 completer 和 numeric-only 判断；
- 重新整理 Decimal Group Variables，只保留仍属于当前 BY / CLASS 的变量。

### 建议测试

1. Builder 绑定 source 并修改独立 Filter。
2. 用包含新增/删除变量的新 handle 模拟 Reload。
3. Filter 内容保持不变。
4. 新变量可以在 completer 中选择。
5. 已删除变量从选择和 Decimal Group 中移除。
6. Run 使用 Reload 后 metadata。

## 2.（已修复）`apply_current_filter()` 保留了旧流程兼容入口

优先级：低
类型：维护性 / 测试语义

### 现状

`ProcMeansBuilder.apply_current_filter()` 仍存在，且旧 UI smoke test 仍直接调用它。当前运行路径已经不再调用该方法，所以不会改变用户行为，但方法名仍然带有旧的“应用当前 Dataset Filter”语义，未来维护时可能被误接回旧确认流程。

### 建议修复

- 如果没有外部兼容需求，删除该方法；或改成语义明确的 `set_filter_text()`。
- 更新测试，直接验证 `filter_editor` / 新 setter，而不是继续锁定旧 API。
- 增加静态断言或回归检查，确保 PROC MEANS controller 中不再出现旧确认提示文本。

## 3.（已补充）Code Generator 的 Filter 回归覆盖可再明确

优先级：低
类型：测试覆盖

### 现状

现有测试已经验证 Builder Filter 能进入共享的 `_proc_means_context()`，从代码结构上 Run/SAS/R 都会获得相同 Config。但测试尚未分别从 SAS 和 R Code Generator 入口验证：

- Generator 获得的是 Builder editor 当前内容；
- Dataset Tab 后续 WHERE 变化不会影响生成代码；
- invalid WHERE 会在进入 Generator 前中止。

### 建议测试

为 SAS 和 R 各补一个轻量 controller test，mock generator/dialog，只检查传入 configuration 的 filter text/AST，不锁死完整生成代码文本。

## 4.（已准备独立清理）本次 commit 意外包含 `.DS_Store`

优先级：低
类型：仓库卫生

### 现状

commit `6989c5e` 除功能文件和测试外，还修改了仓库根目录的 `.DS_Store`。它与 PROC MEANS Filter 功能无关，容易在后续 commit 中产生无意义二进制 diff。

### 建议修复

- 单独恢复或移除该无关变更。
- 后续统一清理已被 Git 跟踪的 `.DS_Store`，并在 `.gitignore` 中忽略它们。
- 不要把此清理与功能修复混在同一个 commit 中。

## 实施方案与边界（已按本轮施工执行）

建议分成三个独立 commit。先修功能生命周期，再补测试/API 清理，最后单独处理仓库卫生。不要把 `.DS_Store` 清理混入 PROC MEANS 功能 commit。

### Phase 1：补齐 PROC MEANS 固定 source 的 Reload 生命周期

目标：Builder 绑定的 Dataset Tab Reload 时，暂停相关操作；Reload 完成后刷新 metadata，但绝不覆盖用户在 Builder 中编辑的 Filter。

#### 涉及模块

| 模块 | 计划修改 |
|---|---|
| `clinical_data_viewer/ui/main_window.py` | 在 Reload 开始、成功完成、失败三个节点通知 `AnalysisController`。 |
| `clinical_data_viewer/controllers/analysis_controller.py` | 增加窄范围 source reload lifecycle 转发入口，不在 MainWindow 中直接操作 Builder。 |
| `clinical_data_viewer/controllers/analysis/proc_means.py` | 识别 Reload 的 tab 是否为当前固定 source；管理 Builder 的暂时不可用状态并在成功后刷新 metadata。 |
| `clinical_data_viewer/ui/proc_means_builder.py` | 增加明确的 source-reloading UI 状态；完善同一 source metadata 刷新与无效变量裁剪。 |
| `tests/test_proc_means_builder_filter.py` | 增加 metadata refresh、Filter 保留和变量裁剪单元测试。 |
| `tests/test_analysis_controller.py` | 增加 controller lifecycle 转发及固定 source 判断测试。 |
| `tests/test_ui_smoke.py` | 增加 MainWindow Reload 与已打开 Builder 的集成测试。 |

#### 具体调用链

```text
MainWindow.reload_current()
        ↓
analysis_controller.source_reload_started(tab)
        ↓
ProcMeansController.source_reload_started(tab)
        ↓
如果 tab 是固定 PROC MEANS source：
禁用 Run / SAS / R / Filter / variable editors
保留全部 Builder 输入
```

Reload 成功且 cache 完整后：

```text
tab.handle 已替换并完成 cache
        ↓
analysis_controller.source_reload_completed(tab)
        ↓
ProcMeansController.source_reload_completed(tab)
        ↓
刷新 Builder metadata
裁剪已不存在/类型不再适用的变量
恢复 Builder 可用状态
Builder Filter 保持原文不变
```

Reload 失败时：

```text
analysis_controller.source_reload_failed(tab)
        ↓
如果旧的完整 cache 仍可用则恢复 Builder；如果已经替换成不完整 cache，则继续保持 disabled
保留 Filter、变量、统计量和 decimal 设置
显示 Reload failed 状态，但不 Clear
```

#### MainWindow 修改细节

1. `reload_current()` 在设置 `tab.reload_in_progress = True` 后调用 `analysis_controller.source_reload_started(tab)`。
2. 初次读取返回 partial handle 时，不要立即把 Builder 恢复为可运行；必须等 `_continue_cache(..., when_complete=...)` 完成。
3. `handle.cache_complete` 为 true 时，在旧 WHERE reapply 流程结束后调用 `source_reload_completed(tab)`。
4. 后台完整 cache 路径中，在 `when_complete(final_handle)` 完成旧 WHERE reapply 后调用相同 completed hook。
5. 初次读取失败和 `_continue_cache()` 失败都调用 `source_reload_failed(tab)`；不能只处理初次读取失败，否则 partial cache 失败会让 Builder 一直 disabled。
6. 通知应使用 Dataset Tab 对象作为 identity，不使用 source path 字符串判断，避免相同路径的不同 Tab 混淆。
7. 旧 WHERE reapply 即使因为 schema 变化而失败，completed hook 也必须在 `finally` 路径执行；否则 Builder 会永久停留在 reloading/disabled 状态。

#### AnalysisController 修改细节

新增窄接口：

```python
def source_reload_started(self, tab: DatasetTab) -> None: ...
def source_reload_completed(self, tab: DatasetTab) -> None: ...
def source_reload_failed(self, tab: DatasetTab) -> None: ...
```

本次只转发给 `self.proc_means`。不要趁机重构其他 analysis modules。其他 Builder 若以后需要相同生命周期，可在独立任务中接入相同 contract。

#### ProcMeansController 修改细节

1. 三个 hook 首先用对象 identity 检查：`self.proc_means_source is tab`。
2. Reload started 时调用 Builder 专用状态方法，例如 `set_source_reloading(True, ...)`。
3. `_proc_means_context()` 增加 `tab.reload_in_progress` guard。即使 UI 状态被绕过，也必须阻止 Run/SAS/R 使用正在 Reload 的 source。
4. Reload completed 时调用 `_set_proc_means_dataset(tab)`，但 Builder 同一 source 分支不得重新读取 `tab.current_where_text()` 覆盖 Builder Filter。
5. Reload failed 时只解除 reloading 状态，不调用 `set_dataset()`，避免意外重置输入。

#### ProcMeansBuilder 修改细节

建议把“首次绑定新 source”和“同 source metadata refresh”明确分开：

- 首次绑定新 source：初始化 Filter 为 Dataset Tab 当前 WHERE，并清空旧变量选择。
- 同 source refresh：Filter 完全不变，统计量不变；只更新 metadata、completer 和变量有效性。
- Clear：清空 Filter 和全部配置，并由现有 signal 释放固定 source。

同 source refresh 前先保存：

```text
Analysis Variables
BY Variables
CLASS Variables
Decimal Group Variables
```

刷新后按新 metadata 处理：

- Analysis Variables 只保留仍存在且仍为 numeric 的变量。
- BY / CLASS 只保留仍存在的变量。
- Decimal Group 只保留仍被选为 BY / CLASS 的变量。
- 变量名应采用新 metadata 中的 canonical spelling。
- 若移除了变量，在 status 中给出简短英文提示，例如：
  `Source reloaded. Removed unavailable selections: AVAL, PARAMCD.`
- 不弹出阻塞式对话框，避免 Reload 完成时连续出现多个 warning。

`VariableTokenEditor.set_metadata(..., preserve=True)` 不能原样保留所有旧字符串；它必须与新 metadata 取交集，并重新执行 `numeric_only` 约束。

Builder 建议由一个 `_update_enabled_state()` 统一计算按钮状态，输入至少包括：

```text
metadata available
calculation busy
source reloading
source kind
```

不要让 `set_busy(False)` 单独把 reloading 状态下的控件重新 enable。

#### Reload 边界与注意事项

- 不修改 Dataset Tab WHERE 的 reapply 行为。
- 不把 Reload 后 Dataset Tab WHERE 再同步给 Builder Filter。
- 不自动 Clear Analysis/BY/CLASS 中仍然有效的选择。
- 不允许在 partial cache 阶段 Run 或生成代码。
- 不修改 `ProcMeansEngine`、`ProcMeansConfig`、JSON、SAS/R generator 或 Filter parser。
- Reload 期间不要复用 `set_busy(True)` 表示计算；计算 busy 与 source reloading 应是两个可组合状态，避免完成回调错误恢复按钮。
- 如果 Reload 与正在运行的 PROC MEANS 冲突，应沿用现有 `_proc_means_input_tabs` guard；在启动 Reload 前明确阻止并提示，不要更换正在被 worker 使用的 source handle。

### Phase 2：清理旧 Filter API 并补齐 Generator 回归测试

目标：删除旧确认流程留下的歧义 API，并从三个用户入口锁定 Builder Filter 的 authoritative 行为。

#### 涉及模块

| 模块 | 计划修改 |
|---|---|
| `clinical_data_viewer/ui/proc_means_builder.py` | 删除 `apply_current_filter()`，或在确有调用需要时改名为 `set_filter_text()`。 |
| `tests/test_ui_smoke.py` | 不再调用旧 `apply_current_filter()`；改为直接设置 editor 或新 setter。 |
| `tests/test_proc_means_builder_filter.py` | 增加 Run/SAS/R 三条入口的 Filter snapshot 回归测试。 |
| `clinical_data_viewer/controllers/analysis/proc_means.py` | 原则上不改业务逻辑；只在测试发现入口不一致时修复。 |

#### 具体步骤

1. 使用 `rg` 确认生产代码没有 `apply_current_filter()` 调用。
2. 删除旧 helper，更新旧 smoke test。
3. 分别调用：
   - `run_proc_means_builder()`
   - `generate_proc_means_sas_code()`
   - `generate_proc_means_r_code()`
4. mock Engine/Generator/Dialog，捕获构建出的 configuration。
5. 验证三条路径的 filter text 与 AST 均来自 Builder editor，而不是 Dataset Tab 当前 WHERE。
6. 将 Dataset Tab WHERE 改成不同条件后重复验证 Builder Filter 未改变。
7. invalid WHERE 下三条路径都必须中止，保留 editor 原文，并只显示明确 validation error。

#### 测试边界

- 不锁死完整 SAS/R code 文本或格式空格。
- 只断言 configuration 中 filter text/AST 和 generator 是否被调用。
- 保留现有 `QMessageBox.question` 不被调用的回归测试，或使用源码/行为测试确保旧提示不会恢复。
- 不重新引入 Dataset Tab 与 Builder Filter 实时同步。

### Phase 3：单独清理 `.DS_Store`

目标：移除无关二进制文件的版本控制噪音，不改任何应用逻辑。

#### 涉及文件

- `.gitignore`
- 当前已被 Git 跟踪的各级 `.DS_Store`

#### 具体步骤

1. 单独列出所有 tracked `.DS_Store`：`git ls-files | rg '(^|/)\.DS_Store$'`。
2. 在 `.gitignore` 增加 `.DS_Store`。
3. 从 Git index 移除 tracked `.DS_Store`，不要求删除开发者本机文件。
4. 确认 `git status` 只包含预期的删除和 `.gitignore` 修改。
5. 使用独立 commit，例如 `Remove tracked macOS metadata files`。

#### 注意事项

- 不改写已推送 commit 历史，不 force push。
- 不把 `.DS_Store` 清理和 PROC MEANS 功能修复放在同一 commit。
- 不删除其他未确认用途的隐藏文件。

## 下一轮必须补充的自动化测试

### Builder 单元测试

1. 首次绑定继承 source WHERE。
2. Builder editor 修改后与 source WHERE 独立。
3. 同 source metadata refresh 保留 Builder Filter。
4. Reload 后新增变量进入 completer。
5. Reload 后已删除变量被裁剪。
6. numeric 变量变为 character 后从 Analysis Variables 移除。
7. BY/CLASS 删除后对应 Decimal Group 同步移除。
8. Clear 清空 Filter、变量选择和 source identity。
9. invalid WHERE 保留输入原文。

### Controller 测试

1. 非固定 source Reload 不影响 PROC MEANS Builder。
2. 固定 source Reload started 后 Builder 不可运行/不可 codegen。
3. `_proc_means_context()` 在 `reload_in_progress` 时拒绝执行。
4. Reload completed 刷新 metadata 并保留 Filter。
5. Reload failed 不 Clear；旧完整 handle 可用时恢复状态，不完整 cache 时继续禁用并要求再次 Reload。
6. Run/SAS/R 都使用相同 Builder Filter configuration。

### MainWindow / UI smoke 测试

1. 打开 ADLB → 打开 Builder → 修改 Builder Filter → Reload ADLB。
2. Reload 期间按钮 disabled。
3. Reload 完成后 Filter 原文仍在，source 仍绑定同一 Tab。
4. 新 metadata 的变量选择状态正确。
5. 切换到其他 Dataset Tab 不改变固定 source 或 Builder Filter。
6. Clear 后才能绑定新的 source，并继承新 source 当前 WHERE。

### 相关回归测试集合

至少执行：

```text
tests.test_proc_means_builder_filter
tests.test_proc_means_builder
tests.test_sas_codegen
tests.test_r_codegen
tests.test_analysis_controller
tests.test_ui_smoke
tests.test_where_filter
tests.test_analysis_and_column_filters
```

随后执行完整套件：

```text
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check clinical_data_viewer tests run.py
.venv/bin/ruff format --check clinical_data_viewer tests run.py
.venv/bin/python -m compileall -q clinical_data_viewer tests run.py
git diff --check
```

## 手工回归验收

在 Windows 打包环境或 Windows 目标机至少完成以下验收：

1. 打开带 WHERE 的 ADLB，打开 PROC MEANS Builder，确认 Filter 首次继承。
2. 在 Builder 中修改 Filter，再修改 Dataset Tab WHERE，确认两者互不覆盖。
3. Run 后核对结果、JSON 与 Builder Filter 一致。
4. 分别生成 SAS/R code，确认 WHERE 来自 Builder Filter。
5. 输入错误 WHERE，确认提示明确且输入保留。
6. Builder 保持打开时 Reload source，确认 Reload 期间不可 Run。
7. Reload 完成后确认 Filter、仍有效的变量和统计量保留。
8. 使用变量 schema 有变化的数据重新生成 source，确认新增变量可选、删除变量不残留。
9. 模拟 Reload 失败，确认 Builder 配置未被 Clear。
10. Clear Builder 后切换到另一 Dataset，确认新 Filter 只继承新 source 当前 WHERE。

## 完成定义

只有同时满足以下条件，才可关闭本 follow-up：

- Reload 生命周期不再留下旧 metadata。
- Reload 期间 Run/SAS/R 均不可执行。
- Builder Filter 在 Reload 和 Dataset Tab WHERE 变化中保持独立。
- 新旧 source 之间不会泄漏 Filter 或变量选择。
- Run、SAS、R、JSON 使用同一 Builder Filter contract。
- 旧确认提示和歧义 API 已清理。
- 自动化相关测试、完整测试、Ruff、compileall、diff check 全部通过。
- Windows 手工 Reload/Run/Codegen 验收通过。
- `.DS_Store` 在独立维护 commit 中处理，不污染功能 commit。

## 明确不在本 follow-up 中修改

- PROC MEANS 统计公式、BY/CLASS semantics、SUBJECT_N。
- Decimal runtime 计算和每统计量 decimal offset。
- PROC MEANS JSON contract。
- SAS/R Generator 的统计实现或模板结构。
- Filter parser、AST 或 SQLite compiler。
- Dataset Tab WHERE 编辑、History 或 Filter Builder。
- 其他 Analysis Builder 的 Reload 生命周期；如需统一，另开架构任务。

## 基线审查验证

```text
.venv/bin/python -m unittest \
  tests.test_proc_means_builder_filter \
  tests.test_proc_means_builder \
  tests.test_analysis_controller \
  tests.test_ui_smoke

Ran 28 tests — OK
```

```text
.venv/bin/python -m unittest discover -s tests

Ran 215 tests — OK
```

完整测试仍出现一条已有的 SQLite `ResourceWarning`，本次审查未发现它由 PROC MEANS Filter 更新引入。

## 本轮施工验证

```text
.venv/bin/python -m unittest \
  tests.test_proc_means_builder_filter \
  tests.test_proc_means_builder \
  tests.test_analysis_controller \
  tests.test_ui_smoke \
  tests.test_sas_codegen \
  tests.test_r_codegen \
  tests.test_where_filter \
  tests.test_analysis_and_column_filters

Ran 74 tests — OK
```

```text
.venv/bin/python -m unittest discover -s tests

Ran 222 tests — OK
```

本轮涉及文件的 Ruff 检查通过，`compileall` 与 `git diff --check` 通过。仓库全量 Ruff/format 仍存在本轮之前的既有问题，未在本任务中顺手修改无关文件。
