# PROC MEANS Builder Variable Controls Width Issue

状态：已实施  
记录日期：2026-08-31

## 1. 用户可见问题

PROC MEANS Builder 按默认 Analysis Dock 宽度打开时，下列 variable controls 与 Builder 可视宽度不匹配：

- Analysis Variables
- BY Variables
- CLASS Variables
- Decimal Group Variables

前三组的输入框和 `Remove` button 位于同一水平行。窗口较窄时，右侧 `Remove` 可能移出可视区域；用户需要把 Analysis Dock 拉得很宽才能操作。

期望行为：

- Builder 以默认宽度打开时即可看到完整输入框和 `Remove` button。
- 四组 variable controls 与 Builder viewport 同宽，不要求用户横向拉伸。
- 窄窗口仍允许上下滚动查看完整页面。
- 不产生不必要的水平滚动条。

## 2. 已确认的涉及程序

### Variable row layout

文件：`clinical_data_viewer/ui/proc_means_builder.py`

`VariableTokenEditor` 当前使用：

```text
QHBoxLayout
├── QLineEdit (stretch=1)
└── Remove button (minimum width 64)
```

该组件用于 Analysis、BY 和 CLASS Variables。虽然单个 editor 的 minimum size hint 并不大，但目前没有一个针对默认 dock viewport 的明确响应式宽度 contract。

Decimal Group Variables 使用独立 `QListWidget`，也需要与前三组共同验证窄宽度行为。

### Builder overall minimum width

文件：`clinical_data_viewer/ui/proc_means_builder.py`

底部按钮当前放在三列 `QGridLayout` 中，其中包括：

- `SAS Code Generator…`
- `R Code Generator…`
- `Run`

实测在当前 Qt/offscreen 环境中：

```text
PROC MEANS Builder minimumSizeHint width: approximately 384px
```

长 Code Generator button 和同一行的按钮组合会把整个 Builder 的 minimum width 撑大。只缩小 variable editor 或 Remove button 不能完整解决问题。

### Analysis Dock width

文件：`clinical_data_viewer/ui/main_window.py`

Analysis Dock 当前设置：

```python
self.analysis_dock.setMinimumWidth(310)
```

因此存在约 `310px` 的默认/允许 dock 宽度与 Builder 约 `384px` minimum width 不一致的问题。Scroll Area 内容可能保持更宽尺寸，导致右端 control 在默认窗口中不可见。

## 3. 已实施方案

本次只重排 PROC MEANS Builder UI，没有修改业务逻辑。

### Step 1：建立窄宽度验收基线

1. 以 Analysis Dock `310px` 为最低正式验收宽度。
2. 同时验证 Builder viewport 扣除 dock/tab/layout margins 后的实际可用宽度。
3. 记录每个 VariableTokenEditor、QLineEdit、Remove button 和 Decimal Group list 的 geometry。

### Step 2：VariableTokenEditor 收缩行为

1. 明确允许 QLineEdit 水平收缩，例如设置合适的 `QSizePolicy` 和 minimum width。
2. Remove button 使用紧凑、稳定的固定/minimum width，保证文字完整但不无谓扩张。
3. layout stretch 只分配给输入框；Remove 永远保留在 viewport 右侧可见区域。
4. 不将 Remove 移到输入框内部，避免影响 completer、Enter 添加和键盘输入。

### Step 3：底部 actions 不再撑大整个 Builder

1. 调整 Settings、Clear、SAS/R Generator 和 Run 的 grid placement。
2. 在窄宽度下优先采用两列或分行排列，避免一行按钮 minimum widths 相加形成约 `384px` 下限。
3. 保持现有按钮名称、signals、busy enabled state 和 codegen availability contract。
4. 不通过裁剪按钮文字来伪装解决问题。

### Step 4：统一四组 variable controls

1. Analysis/BY/CLASS editor 使用相同宽度规则。
2. Decimal Group QListWidget 的右边缘与上述 editors 对齐。
3. 控件宽度跟随 Scroll Area viewport，而不是跟随最长 variable name 无限扩张。
4. 上下滚动必须继续可用。
5. 只有确认所有内容都能真实收缩后，才考虑关闭水平 scrollbar；不要用隐藏 scrollbar 掩盖仍然存在的 overflow。

## 4. 不得改变的行为

- PROC MEANS Engine 与统计计算。
- Builder source 固定和 Clear 生命周期。
- Analysis/BY/CLASS variable validation。
- numeric-only Analysis Variables。
- Enter 添加、comma-separated 添加、completer 和双击删除。
- BY/CLASS 改变后 Decimal Group Variables 的刷新规则。
- Decimal Group selection 与 decimal calculation。
- Filter、Settings、Run、SAS/R Code Generator。
- JSON、SAS/R generation 和 drill-down。

### Step 5：Statistics 与 Filter

1. Statistics 保持两列，但减少左右 margins 和 column spacing。
2. 第一列按内容占宽，第二列紧接排列，避免无意义的大间距。
3. Filter 改为窄宽度可换行的多行输入区，高度保持紧凑。
4. Filter 继续提供原有 `text()` / `setText()` contract，筛选内容与编译行为不变。

## 5. 已增加的测试与验收

已覆盖以下 UI tests：

1. PROC MEANS Builder 在目标 `310px` Analysis Dock 宽度下，三个 Remove buttons 的 `geometry().right()` 均不超过对应 viewport/editor container 的右边缘。
2. Analysis、BY、CLASS QLineEdit 在窄宽度下仍有可输入区域。
3. Decimal Group list 的右边缘不超过 Builder content viewport。
4. Builder 不需要水平滚动即可看到四组 variable controls 的完整宽度。
5. 页面较矮时垂直滚动仍能到达最后一行和底部 actions。
6. Remove button 在窄宽度下仍可删除选中变量。
7. Enter 添加和 completer 不回归。
8. BY/CLASS 更新后 Decimal Group choices 不回归。
9. SAS/R Code Generator 和 Run buttons 在窄宽度下完整可见、可点击。
10. source reload、source temporarily unavailable 和 Clear 不会意外清除/泄漏配置。
11. PROC MEANS Builder 现有状态测试、UI smoke tests 和全量测试通过。

## 6. 注意事项

- 不建议仅提高 Analysis Dock minimum width；这会减少主数据表空间，并不能解决小屏幕或窗口缩小时的适配问题。
- 不建议仅把 Remove 改成图标；用户已明确需要易识别的操作，应该优先解决 layout。
- 不建议直接设置固定的 Builder 总宽度。
- 所有 geometry assertions 应在 widget `show()` 并执行 `QApplication.processEvents()` 后检查，避免读取布局前的无效尺寸。

## 7. 实施结果

PROC MEANS Builder minimum size hint 已降到默认 Analysis Dock 宽度以内；变量 Remove、Decimal Group list、Statistics、Filter 和底部 actions 在 `310px` 验收宽度内可见。水平 scrollbar 不再需要，垂直滚动、变量添加/删除、Filter、Run 和 Code Generator 行为保持不变。
