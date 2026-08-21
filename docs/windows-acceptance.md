# Windows acceptance checklist

在 64 位 Windows 10/11 和最终发布的 `SASDataViewer.exe` 上执行。

1. 打开一个代表性的 SDTM/ADaM `.sas7bdat`，核对 row count、变量名、label、type、length、format 和若干日期/特殊 missing 显示。
2. 加载完成后，在 SAS 中覆盖源文件；随后删除并重新生成源文件。两项操作都必须成功，旧 Tab 仍显示其临时快照。
3. 同时打开至少三个数据集，切换/关闭 Tab，并确认关闭后对应 `%LOCALAPPDATA%\ClinicalDataViewer\temp` 子目录消失。
4. 在 100,000 行以上数据集上滚到底部、切换显示列、正反排序；确认窗口可拖动、菜单可响应。
5. 执行组合 WHERE、IN/NOT IN、CONTAINS、MISSING/NOT MISSING；核对筛选行数。故意输入未闭合引号、未知变量和类型不匹配，确认输入保留且错误清晰。
6. 退出并重启，确认 Filter History 可恢复；测试当前数据集/全局、回填、删除和清空。
7. 筛选后隐藏部分列并排序，导出 CSV；确认 BOM、表头、列集合、行集合、行数和顺序与当前视图一致。
8. 修改源文件后 Reload，确认显示列和有效 WHERE/排序得到保留，并显示新数据。
9. 在打开、Reload 和导出大文件期间持续拖动窗口、切换已有 Tab，确认主线程不冻结。
10. 强制结束进程，等待配置的遗留阈值后重启，确认旧 `cde-*` 会话被清理；近期其他实例的会话不得被删除。

