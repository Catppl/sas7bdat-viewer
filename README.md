# SASDataViewer

独立的 Windows 临床 SAS 数据集浏览器。它只读打开 `.sas7bdat`，采用 PySide6 原生桌面界面、pyreadstat 分块读取和磁盘 SQLite 查询缓存，目标是在日常查看大数据集时保持界面响应。

## 已实现功能

- 同时打开多个 `.sas7bdat`，使用可关闭、可移动的 Tab 切换。
- 源文件先分块复制到本次会话的临时目录；复制结束后，读取、筛选、排序和导出都只访问临时文件/缓存。
- 关闭 Tab 清理对应副本；退出清理整个会话；启动时清理超过 24 小时且不属于仍在运行进程的异常退出遗留目录。
- 大文件复制完成后先读取 metadata 和首批 20,000 行并显示 Tab，其余行继续在后台写入本地缓存；缓存期间仍可浏览首批及随后到达的数据。
- `QTableView + QAbstractTableModel` 虚拟行模型，每页默认 500 行、内存中只保留有限页面；可以直接滚动/跳到远端页面，不需要先加载前面的所有行。
- 可折叠 Variables 面板、显示列勾选、Select All、变量定位、变量搜索，以及 Variable/Label/Type/Length/Format metadata。
- 手写 SAS-like WHERE；支持列与常量或列与列比较，例如 `AESTDTC <= AEENDTC`。
- 比较符支持 `=`/`EQ`、`!=`/`^=`/`~=`/`<>`/`NE`、`>`/`GT`、`>=`/`GE`、`<`/`LT`、`<=`/`LE`，以及字符前缀修饰符 `=:` 等。
- 逻辑与条件支持 `AND`/`&`、`OR`/`|`/`!`、`NOT`/`^`/`~`、`IN`、`NOT IN`、`BETWEEN ... AND ...`、`CONTAINS`/`?`、`LIKE`、`IS NULL`、`IS MISSING`、`MISSING()` 和括号。
- `Ctrl+Enter` 或 Apply 执行；语法/类型/变量错误会指出原因和位置，并保留输入。
- 成功 WHERE 历史持久化；支持当前数据集/全部数据集、回填、单条删除和清空。列头互动筛选会同步生成可编辑的 SAS-like WHERE，并与手写条件作为一条完整条件保存。
- CSV 仅导出“当前筛选结果 + 当前显示列”，并保持当前排序；编码为 UTF-8 BOM，后台分批写出。
- 列头右侧的筛选箭头提供 Excel 风格互动筛选：可搜索/勾选当前值，也可按 `=`、`!=`、大小比较、Between 和 Contains 设置条件。不同列之间按 AND 组合，并与手写 WHERE 一起生效；完整条件会同步到 WHERE 编辑框，蓝色筛选标签可逐列清除。
- 数值列右键提供 `PROC MEANS`；统计基于“手写 WHERE + 列头筛选”后的完整结果，后台计算固定 `USUBJID` 受试者数、非缺失 N、NMISS、Mean、SD、SE、Median、Q1、Q3、Min、Max 和均值 Student-t CI。
- `Tools > Analysis` 可随时显示/隐藏右侧 Analysis 面板；右键 `Settings…` 或 Tools > Settings 可分别设置 Mean、SD、SE、Median、Q1、Q3、Min、Max 和 CI 等统计量的 0–10 位小数，并设置显示统计量和置信水平。计数类 n/N/NMISS 保持整数；显示采用 `ROUND_HALF_UP` 四舍五入，不改变计算值或原值；非数值列的 PROC MEANS 菜单自动禁用。
- 在行号区域用 `Ctrl+click` 可非连续选择 2–20 行，然后右键 Compare Selected Rows；程序比较所有变量，只在参与比较的行中用浅黄色标出有差异的单元格，并在右侧 Analysis > Row Comparison 列出各行值。
- `Ctrl+F` 在当前筛选、排序结果的当前显示列中查找文本；`F3`/`Shift+F3` 查找下一个/上一个；`Ctrl+G` 按当前结果行号跳转。
- Reload 从原始路径生成新副本，并尽量保留显示列、WHERE 输入和已应用筛选；大文件重新缓存完成后再应用 WHERE。
- 文件复制、SAS 读取、缓存构建、查询筛选、Reload 和 CSV 导出均通过 Qt 线程池运行。

界面采用紧凑的传统 Windows 桌面布局：菜单栏、图标工具栏、数据集 Tab、左侧 Variables、右侧数据表、底部 WHERE、最底部状态栏。左侧 Filter variables 与右上角 Search Variable 同步。

![SASDataViewer 浅蓝主题、列筛选和统计面板](docs/screenshots/SASDataViewer-analysis.png)

## 开发运行

要求 64 位 Python 3.11 或更新版本；目标电脑的 Python 3.11.5 可直接使用。项目只声明下限 `>=3.11`，没有把 3.11.5、3.12 或某个最高版本写死。实际能否安装仍取决于 PySide6 和 pyreadstat 是否为该 Python 版本提供 wheel，内部发布建议固定使用已经验收过的 Python 3.11.5 构建 EXE。

```powershell
cd C:\path\to\sas7bdat-viewer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m clinical_data_viewer
```

运行不依赖 SAS，也不会执行 SAS 程序。pyreadstat 对自定义 format catalog、特殊 missing、编码和某些日期显示方式可能与 SAS 本身不同；本工具定位为只读日常浏览器，不用于监管输出计算。

## 操作

- Open：可一次选择一个或多个文件。
- 命令行/文件关联：程序接受一个或多个 `.sas7bdat` 路径，例如 `SASDataViewer.exe "C:\project data\中文\adae.sas7bdat"`。Windows 将扩展名关联到该 EXE 后，双击数据集会启动一个 Viewer 窗口并自动打开传入文件；当前版本不把新文件转交给已经运行的窗口。
- 表头：首次点击升序，再次点击降序；相同行值按源行顺序稳定显示。
- 列头筛选：点击列名主体仍然排序；点击表头最右侧 `▼` 打开当前列筛选。当前列候选值会遵循手写 WHERE 和其他列筛选。高基数列最多载入前 2,000 个候选值，此时取消选中的值按排除条件处理，也可切换 Condition 精确输入。成功应用后，编辑框会生成如 `FOLDERSEQ in (3, 4)` 的完整 SAS-like WHERE；自动生成的关键词使用小写，单个条件不增加多余外层括号，只有明确勾选 Missing 才会生成 `or missing(FOLDERSEQ)`。
- Copy：选择单元格、整行或矩形区域后按 `Ctrl+C`；右键可复制列名。
- PROC MEANS：在数值列单元格上右键选择 PROC MEANS；结果显示在右侧 Analysis。小写 `n (Subjects)` 是当前过滤结果中该分析变量非缺失且 `USUBJID` 非缺失的唯一受试者数；`N (Values)` 才是分析变量非缺失观测数。若没有 `USUBJID`，受试者数显示为不可用，不会用其他列替代。
- Row Comparison：点击左侧行号选择整行，按住 `Ctrl` 点击其他行号进行非连续多选，右键 Compare Selected Rows。黄色背景仅应用到选中比较行中有差异的列，并会覆盖这些单元格原有的蓝色选中背景；相同行的其他列仍保持蓝色，未选行不变。字符空值和 NULL 都视为 missing；数值按未格式化原值比较。筛选、排序或 Reload 后旧比较自动清除。
- Variables：顶部列表是当前显示列；展开 All Variables 可查看完整 metadata 并勾选隐藏列。Select All 在“全选”和“全部取消”之间切换；部分选择或全部取消后再次点击会恢复全部变量。允许暂时隐藏全部列，此时 Apply、Find、Go to Row 和 Export 不可用。
- WHERE：`Ctrl+Enter`、Apply 执行；Clear 不修改数据，只恢复完整显示。列头筛选生成的条件可以继续手工编辑；手工改写后 Apply 会把编辑框整体作为唯一条件来源，并清除旧筛选标签，避免同一条件重复执行。
- Find：`Ctrl+F` 打开查找栏，Enter 或 `F3` 查找下一个，`Shift+F3` 查找上一个。查找范围始终是当前筛选结果和当前显示列。
- Go to Row：`Ctrl+G` 输入当前结果中的 1-based 行号。虚拟表格会直接请求对应页面。
- Filter History：双击或 Use Condition 只会回填 WHERE，需 Apply 后执行。历史保存完整的手写 WHERE 与列头筛选条件；从历史恢复时不重建列头复选状态，而是将等价 WHERE 作为手写条件执行。
- Export CSV：弹出 Windows Save As，导出时界面仍可使用。
- Reload：源文件不存在或新内容读取失败时保留旧 Tab 数据。

大文件的完整缓存尚未完成时，表格会显示蓝色提示和 `Rows cached` 状态。此阶段允许浏览、复制和调整显示变量；筛选、排序、全文查找、行跳转和 CSV 导出会暂时禁用，因为这些操作必须基于完整数据才不会产生误导结果。缓存完成后自动启用，无需重新打开文件。

## 源文件与临时文件

Windows 默认目录：

```text
%LOCALAPPDATA%\ClinicalDataViewer\temp\cde-<time>-<pid>-<id>\<dataset-id>\
```

`TempManager.copy_dataset()` 对源文件使用局部 `with open(..., "rb")`。句柄在复制完成或失败时立即关闭；后续 pyreadstat 只打开临时副本。因此加载完成后 SAS 可以覆盖、删除或重新生成源文件。Reload 会再次短暂打开源文件并创建一份新的、完整缓存；成功后再淘汰旧副本。

历史和设置位于：

```text
%LOCALAPPDATA%\ClinicalDataViewer\filter_history.sqlite
%LOCALAPPDATA%\ClinicalDataViewer\settings.json
```

历史仅保存原始路径、文件名、WHERE 和 UTC 时间，不保存任何数据行。

## 如何验证

验证分为自动化检查、界面运行检查和真实数据验收。最终发布前应在 Windows 10/11 64 位机器上完成全部三部分。

### 1. 准备验证环境

打开 PowerShell，进入项目目录：

```powershell
cd C:\path\to\sas7bdat-viewer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
```

确认运行环境：

```powershell
python --version
python -c "import PySide6, pyreadstat; print('PySide6 OK'); print('pyreadstat', pyreadstat.__version__)"
```

### 2. 运行自动化检查

```powershell
ruff check clinical_data_viewer tests run.py
ruff format --check clinical_data_viewer tests run.py
python -m compileall -q clinical_data_viewer tests run.py
python -m unittest discover -s tests -v
```

预期结果：

- Ruff 显示 `All checks passed!`。
- `compileall` 没有错误输出，并返回成功。
- 所有单元测试显示 `OK`；安装 PySide6 后 UI smoke test 不应被跳过。
- 测试覆盖 WHERE 解析和类型校验、参数化 SQL、分页筛选排序、当前视图 CSV 和 UTF-8 BOM、Filter History 恢复/去重、设置持久化、临时副本及遗留目录清理。

### 3. 启动并查看界面

```powershell
python -m clinical_data_viewer
```

应看到浅蓝色 Windows 11 / SAS Studio 风格主界面。检查 Open、Reload、Export CSV、Clear Filter、Filter History、Variables 面板、WHERE 编辑器和最底部状态栏都可见。

### 4. 使用真实 SAS 数据验收

请使用测试副本，不要直接拿唯一一份生产数据做删除测试。

1. 用 Open 同时选择 2–3 个 SDTM/ADaM `.sas7bdat`，确认每个文件生成独立 Tab。
2. 核对总行数、变量数，以及 Variable、Label、Type、Length、Format metadata。
3. 先取消 Select All，再次点击 Select All，确认全部变量能恢复；逐个勾选/取消变量，确认主表立即只显示当前变量；点击变量应定位对应列。
4. 点击表头测试升序/降序；选择单元格、整行和矩形区域后按 `Ctrl+C`。
5. 执行组合 WHERE，例如：

   ```text
   AESER = "Y" and TRTEMFL = "Y" and USUBJID = "101-001"
   ```

6. 分别验证 `IN`、`NOT IN`、`CONTAINS`/`?`、`AND`/`&`、`OR`/`|`、`BETWEEN`、`LIKE`、`IS NULL/MISSING`、比较助记符和括号。
7. 验证列对列条件，例如 `AESTDTC <= AEENDTC`，并确认字符列与数值列比较会给出明确类型错误。
8. 点击不同列的筛选箭头，分别验证 Values、Missing、数值 Between 和字符 Contains；只选 `PARAMCD=ALB` 时 WHERE 应为 `PARAMCD in ("ALB")`，不得自动追加 missing 或多余外层括号；明确勾选 Missing 后才出现小写的 `or missing(PARAMCD)`。确认状态行数、CSV、PROC MEANS 和 Filter History 都使用同一最终结果。手工修改生成条件再 Apply，确认旧蓝色筛选标签清除且条件不重复。
9. 在数值列右键运行 PROC MEANS，核对当前筛选结果的受试者 n、观测 N、均值、分位数和 CI；在非数值列确认 PROC MEANS 禁用。分别设置 Mean=2、SD=3、Median=1、Min/Max=0 位小数，确认各统计量独立显示且重启后恢复，底层统计结果不变。
10. 在行号上用 Ctrl 非连续选择 2–20 行，运行 Compare Selected Rows；确认黄色只出现在所选行与差异列的交叉单元格，未选行不变；隐藏变量的差异仍出现在 Analysis 面板中。
11. 按 `Ctrl+F` 查找当前显示文本，并用 `F3`/`Shift+F3` 前后查找；按 `Ctrl+G` 跳到第 1 行、末行和一个远端中间行。
12. 故意输入未闭合引号、未知变量和错误类型，确认显示清楚的错误且 WHERE 原文仍保留。
13. 关闭程序再启动，确认成功执行过的 WHERE 能从当前数据集历史和全局历史恢复。
14. 直接运行 `SASDataViewer.exe "C:\clinical test\中文\adae.sas7bdat"`，确认程序自动打开该文件；再把 `.sas7bdat` 关联到 EXE 后双击验证。文件名包含空格和中文时也应成功。

完整人工检查表也保存在 [docs/windows-acceptance.md](docs/windows-acceptance.md)。

### 5. 验证源文件没有被长期占用

先创建专用测试副本和备份：

```powershell
New-Item -ItemType Directory -Force .\manual-test | Out-Null
Copy-Item "C:\clinical-test\adae.sas7bdat" ".\manual-test\lock-test.sas7bdat"
Copy-Item ".\manual-test\lock-test.sas7bdat" ".\manual-test\lock-test-backup.sas7bdat"
```

在 SASDataViewer 中打开 `manual-test\lock-test.sas7bdat`，等加载完成后，在另一个 PowerShell 窗口执行：

```powershell
Remove-Item ".\manual-test\lock-test.sas7bdat"
Copy-Item ".\manual-test\lock-test-backup.sas7bdat" ".\manual-test\lock-test.sas7bdat"
```

两条命令都应成功，已打开的 Tab 仍应能浏览旧快照。这证明加载完成后程序不再持有原文件。此处删除的只是明确创建的测试副本。

### 6. 验证 Reload、CSV 和临时清理

- 修改或重新生成测试源文件，点击 Reload；确认显示新内容，并尽量保留 WHERE 和显示变量。大文件缓存完成后 WHERE 应自动重用。
- 先筛选、隐藏若干变量并排序，然后 Export CSV；确认导出的行数、行顺序和列集合与当前视图完全一致，文件开头包含 UTF-8 BOM。
- 加载过程中拖动窗口、切换其他 Tab；导出大 CSV 时继续操作界面，确认没有主线程冻结。
- 用行数超过 20,000 的数据集验证：首批行显示后 Tab 可立即浏览，底部缓存行数持续增加；缓存完成前筛选/排序/查找/跳转/导出不可用，完成后自动恢复。
- 打开文件后检查 `%LOCALAPPDATA%\ClinicalDataViewer\temp`；关闭对应 Tab 后其 dataset 子目录应消失，正常退出后本次 `cde-*` 会话目录应消失。

## 如何打包为 Windows EXE

Windows EXE 必须在 Windows 64 位环境构建；macOS 不能用 PyInstaller 直接交叉生成可用的 Windows EXE。目标环境建议使用已经验收的 Python 3.11.5 64 位，但构建配置没有限定到这个补丁版本。

### 方法一：使用自动构建脚本（推荐）

在项目根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1
```

脚本不会写死 `py -3.12`。它优先使用当前 `python`，其次使用 Windows `py` launcher，也可以明确传入任意 Python 3.11+ 解释器而不修改脚本：

```powershell
.\scripts\build_windows.ps1 -PythonExe "C:\Path\To\Python311\python.exe"
```

如果已有 `.venv` 来自另一个 Python，需要重建时：

```powershell
.\scripts\build_windows.ps1 -PythonExe "C:\Path\To\Python311\python.exe" -RecreateVenv
```

脚本会依次：

1. 创建 `.venv`（不存在时）。
2. 安装 PySide6、pyreadstat、SciPy、Ruff 和 PyInstaller。
3. 运行 Ruff、格式、Python 编译和全部单元测试。
4. 仅在检查全部通过后构建 one-file GUI EXE。
5. 输出 EXE 的完整路径、大小和 SHA256。

EXE、窗口标题栏和 Windows 任务栏图标使用 [assets/SASDataViewer.ico](assets/SASDataViewer.ico)。该文件由用户提供的 [原始 PNG](assets/SASDataViewer.png) 生成，内含 16、20、24、32、40、48、64、128 和 256 像素尺寸；正常构建无需再次手工转换图标。

生成文件：

```text
dist\SASDataViewer.exe
```

### 方法二：手动打包

```powershell
cd C:\path\to\sas7bdat-viewer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
ruff check clinical_data_viewer tests run.py
ruff format --check clinical_data_viewer tests run.py
python -m compileall -q clinical_data_viewer tests run.py
python -m unittest discover -s tests -v
pyinstaller --noconfirm --clean SASDataViewer.spec
```

### 打包后验证

```powershell
Get-Item .\dist\SASDataViewer.exe | Select-Object FullName, Length, LastWriteTime
Get-FileHash .\dist\SASDataViewer.exe -Algorithm SHA256
Start-Process .\dist\SASDataViewer.exe
```

验证 EXE 能接收 Windows 文件关联传入的路径：

```powershell
& .\dist\SASDataViewer.exe "C:\clinical test\中文\adae.sas7bdat"
```

程序本身不会静默修改注册表。请通过 Windows“打开方式 > 选择其他应用 > 始终使用此应用”关联 `.sas7bdat`，或由组织的安装包注册文件关联；关联命令必须包含带引号的 `%1`，典型形式为 `"C:\path\SASDataViewer.exe" "%1"`。当前版本采用多实例行为：双击文件会新开一个 Viewer 窗口，不会转交给已经运行的窗口。

然后使用 `dist\SASDataViewer.exe` 重复上面的真实数据、源文件占用、历史恢复、筛选导出、Reload 和临时目录清理检查。至少还应在一台没有 Python/项目源码的干净 Windows 机器上启动一次，确认 EXE 确实独立运行。

这是未签名的 windowed one-file EXE：首次启动可能因为解包稍慢，Windows SmartScreen 也可能显示未知发布者提示。正式内部发布如需消除此提示，应使用组织的代码签名证书签名。若内部环境禁止 UPX，可在 `SASDataViewer.spec` 中把 `upx=True` 改为 `False` 后重新构建。

## 项目结构

```text
clinical_data_viewer/
  ui/                 PySide6 主窗口、数据 Tab、Variables、历史与复制表格
  sas_reader.py       pyreadstat metadata/分块读取与 SQLite 缓存
  temp_manager.py     源文件临时复制、会话清理、遗留清理
  table_model.py      QAbstractTableModel lazy loading
  where_parser.py     SAS-like WHERE lexer/parser
  filter_engine.py    metadata 校验与参数化 SQL
  column_filters.py   列头互动筛选及与手写 WHERE 的组合
  statistics.py       PROC MEANS 风格统计、QNTLDEF=5 和 Student-t CI
  filter_history.py   持久化历史
  csv_exporter.py     当前视图后台 CSV 输出
  settings.py         用户设置与 Windows 路径
  resources.py        源码与 PyInstaller bundle 的图标资源定位
  workers.py          QThreadPool/QRunnable worker
assets/               应用原始 PNG 与多尺寸 Windows ICO 图标
tests/                无需真实 SAS 文件的核心回归测试
```
