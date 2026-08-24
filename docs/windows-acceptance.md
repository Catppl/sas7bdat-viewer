# Windows acceptance checklist

在 64 位 Windows 10/11 和最终发布的 `SASDataViewer.exe` 上执行。

1. 打开一个代表性的 SDTM/ADaM `.sas7bdat`，核对 row count、变量名、label、type、length、format 和若干日期/特殊 missing 显示。
2. 加载完成后，在 SAS 中覆盖源文件；随后删除并重新生成源文件。两项操作都必须成功，旧 Tab 仍显示其临时快照。
3. 同时打开至少三个数据集，切换/关闭 Tab，并确认关闭后对应 `%LOCALAPPDATA%\ClinicalDataViewer\temp` 子目录消失。
4. 在 100,000 行以上数据集上确认首批 20,000 行先显示、后台缓存计数持续增加；缓存期间窗口可响应，完成前全量筛选/排序/查找/跳转/导出禁用，完成后自动启用。
5. 取消 Select All 后再次点击，确认全部变量恢复；再测试逐列隐藏、隐藏全部列和重新全选。
6. 滚到底部并正反排序；按 `Ctrl+G` 直接跳到远端中间行，确认无需先载入全部前置页面。
7. 执行列对常量和列对列 WHERE，包括 `AESTDTC <= AEENDTC`、`IN/NOT IN`、`CONTAINS`/`?`、`AND`/`&`、`OR`/`|`、`BETWEEN`、`LIKE`、`IS NULL/MISSING`；核对筛选行数。故意输入未闭合引号、未知变量和类型不匹配，确认输入保留且错误清晰。
8. 按 `Ctrl+F` 查找当前显示文本，使用 `F3`/`Shift+F3` 前后查找，确认只搜索当前筛选结果和当前显示列。
9. 退出并重启，确认 Filter History 可恢复；测试当前数据集/全局、回填、删除和清空。列头筛选成功后，历史应保存手写条件与列头条件合成的完整 WHERE；恢复后 Apply 应得到相同结果。
10. 筛选后隐藏部分列并排序，导出 CSV；确认 BOM、表头、列集合、行集合、行数和顺序与当前视图一致。
11. 修改源文件后 Reload，确认显示列和有效 WHERE 得到保留，并显示新数据。
12. 在打开、Reload 和导出大文件期间持续拖动窗口、切换已有 Tab，确认主线程不冻结。
13. 强制结束进程，等待配置的遗留阈值后重启，确认旧 `cde-*` 会话被清理；近期其他实例的会话不得被删除。
14. 点击表头文字区域确认仍排序；点击右侧 `▼` 确认只打开筛选。验证 Values 搜索、Select All、Missing、数值 Between、字符 Contains 和蓝色筛选标签；WHERE 框应同步显示如 `FOLDERSEQ IN (3, 4)` 的 SAS-like 条件。
15. 同时应用手写 WHERE 与两个列头筛选，确认状态行数、Ctrl+F、PROC MEANS、CSV 和 Filter History 都使用相同的最终结果。直接按 Apply 不应重复叠加条件；手工修改生成的 WHERE 后 Apply，应清除旧蓝色筛选标签并以编辑框整体为准。
16. 非数值列右键 PROC MEANS 应禁用；数值列应在后台计算。核对固定 `USUBJID` 的 n、观测 N、NMISS、Mean、SD、SE、QNTLDEF=5 分位数、Min/Max 和 Student-t 均值 CI。
17. 修改 PROC MEANS 的小数位、置信水平和显示统计量，重启后确认设置恢复；数值显示四舍五入但源值不变。
18. 在行号上按 Ctrl 非连续选择 2–20 行并比较；浅黄色只能出现在所选行与差异列的交叉单元格，未选中的其他行不得高亮，隐藏变量差异仍在 Analysis 面板列出。
19. 改变筛选、排序、Reload 或关闭 Tab，确认旧行比较和高亮被清除。
20. 从 PowerShell 运行 `SASDataViewer.exe "C:\clinical test\中文\adae.sas7bdat"`，确认含空格/中文的传入文件自动打开。再用 Windows“始终使用此应用”关联 `.sas7bdat` 后双击文件，确认新 Viewer 窗口自动打开该数据集；加载后源文件仍可覆盖和删除。
21. 从 Filter History 回填包含列头筛选的完整 WHERE 并 Apply，确认结果一致。当前版本不要求恢复原来的列头复选框/蓝色标签，只要求条件语义与结果一致。
