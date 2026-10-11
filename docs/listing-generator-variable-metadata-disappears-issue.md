# Listing Generator 变量与 metadata 消失 issue

记录日期：2026-08-31  
状态：已记录，暂未修改程序

## 1. 用户可见现象

打开或使用 `Tools > Listing Generator` 时：

- Variable 选择框中不再显示任何 source variables；
- 下方用于核对变量 label、type、length、format 等 metadata 的区域也没有内容；
- 因此用户无法正常选择 source variable，也无法确认变量属性。

期望行为：

- Listing Builder 打开后由用户从已经打开的数据集中选择 source；
- Variable 选择框始终展示用户所选 source 的全部可用变量；
- 选中变量后能够展示对应 metadata；
- 切换主界面的其他 dataset tab 不应清空 Listing Builder 的选择、变量或已填写配置；
- source 被关闭后 selector 返回未选择状态，但已填写配置继续保留；用户可重新选择其他已打开
  dataset；
- 只有用户点击 Listing Builder 自己的 `Clear` 才清空用户配置。

## 2. 当前代码检查结果

### Listing Builder 内部状态

涉及文件：

- `clinical_data_viewer/ui/listing_builder.py`
- `clinical_data_viewer/controllers/analysis/listing.py`

当前 `ListingBuilder.set_dataset()` 会从 `DatasetMetadata.variables` 填充
`variable_picker`。Builder 内部明确清空 metadata 和变量框的路径是
`ListingBuilder.clear()`：

1. `self._metadata = None`
2. `self.variable_picker.clear()`
3. 发出 `cleared` signal
4. `ListingController.clear_listing_source()` 解除固定 source

`set_dataset(None, ...)` 在 source 暂时不可用时只禁用 Run/Code Generator，按当前
实现不会主动清空已保存的 `_metadata` 或变量下拉框。

### 主界面 Variables/metadata 面板

涉及文件：

- `clinical_data_viewer/ui/variables_panel.py`
- `clinical_data_viewer/ui/main_window.py`

主界面 Variables 面板由 `MainWindow._sync_active_tab()` 根据当前 Dataset Tab 的
`metadata`、`visible_columns` 和 `available_columns()` 重建。它与 Listing Builder
自己的 `variable_picker` 是两套状态来源。

因此，“Listing variable 选择框”和“主界面 metadata 区域”同时为空，当前最需要
调查的是：

- Listing Builder 的固定 source 是否被意外解除或错误调用了 `clear()`；
- 打开 Listing Builder 或 Listing Result 后，active Dataset Tab 是否暂时变成空值；
- controller 迁移后的 source refresh/reload/close 回调是否把可用 metadata 错误地传成
  `None`；
- 如果用户所指的 metadata 区域属于 Listing Builder 本身，需要先确认当前 UI 版本，
  因为现有 `ListingBuilder` 代码只有变量下拉框，没有独立的 metadata preview 控件。

当前尚未取得稳定复现步骤，所以以上是定位范围，不把任何一项提前认定为最终根因。

## 3. 后续修复施工步骤

### Step 1：建立稳定复现矩阵

逐项记录 Variable 列表、metadata 区域、Builder source label 和现有 Columns 配置：

1. source dataset 完成加载后首次打开 Listing Generator；
2. Listing Generator 保持打开并切换到其他 dataset tab；
3. 再切回原 source tab；
4. reload 原 source dataset；
5. source cache 暂时 unavailable 后恢复；
6. Run Listing 并切换到 `Listing Result`；
7. 关闭 Listing Result；
8. 尝试关闭固定 source dataset；
9. 用户主动点击 Listing Builder 的 `Clear`。

必须先确认是 Builder 下拉框、主 Variables 面板，还是两者都丢失。

### Step 2：确认状态断点

在不改变业务逻辑的前提下检查：

- `ListingController.show_listing_builder()`
- `ListingController._set_listing_dataset()`
- `ListingController.clear_listing_source()`
- `AnalysisController.source_reload_started()` / reload 完成回调
- `ListingBuilder.set_dataset()` / `ListingBuilder.clear()`
- `MainWindow._sync_active_tab()`
- `VariablesPanel.set_dataset()`

对比三个状态：

- controller 保存的当前用户选择 `_listing_source`；
- Builder 保存的 `_metadata`；
- 当前 Dataset Tab 提供给 VariablesPanel 的 metadata。

### Step 3：最小修复

根据实际断点选择局部修复，必须遵守：

- 不因切换 tab 清空 Listing Builder；
- 不自动绑定当前 active tab，source 必须由用户在 Builder selector 中选择；
- source 暂时 unavailable 时保留 Builder metadata、Columns 和用户输入，只禁用 Run；
- source reload 完成后用新 metadata 重新填充 variable picker；
- schema 变化时只处理已不存在的引用，不整体清空配置；
- source 被关闭时恢复为未选择状态，不静默改绑到当前 tab，并保留用户配置；
- 用户点击 `Clear` 才执行完整清空和解除 source；
- 如果缺少 Listing 内部 metadata preview，只在确认用户所指确为该控件后再补 UI，避免
  把主界面 VariablesPanel 重复实现一遍。

## 4. 修改边界

本 issue 修复时不要修改：

- Listing Python Engine；
- Listing expression AST；
- Listing JSON contract；
- ADSL merge semantics；
- SAS Code Generator/Jinja2 template；
- Listing result schema；
- 其他 Analysis Builder 的 source lifecycle。

## 5. 必需测试

### Listing Builder UI/state tests

1. 首次绑定 DatasetMetadata 后，Variable 选择框包含全部 source variables；
2. 选中变量时 metadata 展示正确（若确认该展示属于 Listing Builder）；
3. 用户可以从已打开 dataset selector 中明确选择 Listing source；
4. 切换主 dataset tab 不改变用户选择的 Listing source；
5. source 暂时 unavailable 时变量列表和用户 Columns 配置仍保留；
6. source reload 后变量列表按新 metadata 刷新；
7. reload 后仍存在的 Columns 配置保留；
8. 只有点击 `Clear` 才清空用户配置；
9. source 关闭后不自动绑定其他 dataset，配置仍保留；
10. Run Listing / 打开 Listing Result / 关闭 Result 不清空 Builder variable picker。

### VariablesPanel integration tests

1. Dataset Tab active 时 VariablesPanel 显示全部 available variables 和 metadata；
2. Listing Result active 时显示 Listing Result 自己的 output variables；
3. 返回原 source tab 后恢复 source metadata；
4. Analysis Builder 的显示/隐藏不应将 active Dataset Tab 同步为空。

### 回归验收

- 运行 `tests/test_listing_builder_state.py`；
- 运行 Listing controller/source lifecycle 相关测试；
- 运行 VariablesPanel/MainWindow tab switching 相关测试；
- 运行 Listing Engine、result store、configuration 和 SAS generator tests；
- 最后运行项目全量测试、Ruff 和 `git diff --check`。

## 6. 完成标准

- Variable 选择框稳定展示用户所选 source 的变量；
- metadata 信息可以正常查看；
- tab 切换、Run、Result 打开/关闭及暂时 reload 不再清空 Builder；
- source 真正不可用时给出明确 warning；
- `Clear` 仍是唯一主动清空完整 Listing 配置的操作；
- Listing 计算、JSON 和 SAS 生成结果无变化。
