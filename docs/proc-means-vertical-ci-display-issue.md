# PROC MEANS Vertical CI display issue

## Issue

纵向 `PROC MEANS Table Builder (Vertical)` 当前将均值置信区间显示为两行：

- `CI Lower`
- `CI Upper`

需要改为临床表格更常用的单行显示：

```text
95% CI Mean    lower, upper
```

其中 `lower` 和 `upper` 均按一位小数显示，例如：

```text
95% CI Mean    81.2, 84.7
```

## 保持不变

- CI 计算语义保持现有定义：默认双侧 95% Student-t mean confidence interval。
- `confidence` 配置仍决定实际置信水平；如果用户将其改为其他水平，标签应相应动态显示，例如 `90% CI Mean`。
- 不修改 Python 统计公式、SAS/R code generator 的计算逻辑或 JSON contract。
- 当有效观测数不足以计算 CI 时，继续沿用现有 missing display 语义。

## 后续实施计划

1. 在纵向 PROC MEANS 的统计行组装逻辑中，将 `lclm` 与 `uclm` 合并为一个显示行。
2. 标签由实际 `confidence` 动态生成，默认显示为 `95% CI Mean`。
3. 该行的显示值按 `lower, upper` 顺序输出，并固定使用一位小数；这只是该表布局的显示规则，不改变底层数值精度。
4. 同步检查纵向结果 Tab、long result、CSV/导出显示和 drill-down metadata，确保统计行名称变化后仍能定位原始记录。
5. 如 SAS code generator 生成纵向表格显示行，也同步调整最终 display label/format；底层 `LCLM`/`UCLM` 计算继续保持独立。

## Tests

- 默认 `confidence=0.95` 时标签为 `95% CI Mean`。
- 非默认置信水平时标签使用对应百分比。
- CI 数值按 `lower, upper` 顺序显示并保留一位小数。
- 验证 CI 计算值未因显示合并而改变。
- 验证缺少有效观测时仍显示 missing，而不是伪造区间。
- 验证结果 Tab、long result、导出和 drill-down 不受影响。
- 运行纵向 PROC MEANS 相关回归测试及全量测试。

## Additional UI issue: Builder width

当前纵向 PROC MEANS Builder 在横向展开后仍可能出现内容显示不完整的问题。后续需要将 Builder 的默认 Analysis Dock 宽度控制在主窗口宽度的大约四分之一至三分之一，并确保常用控件在默认宽度下可见。

实施时只调整 UI 布局和 Dock 初始宽度，不修改统计、配置、结果或 code generator 逻辑。应优先保证：

- Analysis Dock 默认宽度不再占用过宽的数据浏览区域；
- Builder 内部内容在目标宽度下不产生不必要的横向截断；
- 垂直滚动仍可访问页面底部的 Settings、Clear、SAS Code 和 Run；
- 用户仍可以手动拖动 Dock 调整宽度；
- 其他 Builder 的宽度和布局不受影响。

对应 UI 回归测试应覆盖默认宽度、内容可见性、垂直滚动以及四个底部按钮的可见和可点击状态。

