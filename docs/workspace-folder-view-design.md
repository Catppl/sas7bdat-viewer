# Workspace Folder Panel 更新设计

设计日期：2026-10-11  
项目：`sas7bdat-viewer / SASDataViewer`  
状态：已按本设计实施；本文同时作为维护和回归验收依据

## 1. 需求理解

用户希望在现有 `Open` 入口旁增加一个文件夹级入口：

```text
Open Folder as Workspace…
```

选择一个文件夹后，程序显示 `Folder Panel`，列出该文件夹内可打开的 SAS 数据集。
用户可以从 Folder Panel 直接打开一个或多个数据集，也可以收藏常用 Workspace，下一次启动程序后快速重新进入该文件夹。

这里建议统一使用术语 **Workspace**，不是 `Workplace`。Workspace 在 v1 中只代表一个用于浏览数据集的文件夹，不代表保存当前 Tab、WHERE、分析配置或程序会话的完整项目文件。

本更新不改变数据集读取逻辑：

```text
Workspace folder
    ↓ 只发现文件
Folder Panel
    ↓ 用户明确选择 Open / 双击
现有 MainWindow.open_paths()
    ↓
现有 TempManager + SasDatasetReader
    ↓
Dataset Tab
```

Folder Panel 只索引文件路径并显示文件名，不展示文件类型、大小、修改时间或打开状态；不预读 SAS metadata，不创建 SQLite cache，也不自动打开文件。

## 2. 当前代码基础

现有实现中可直接复用：

- `MainWindow.open_files()`：多文件选择入口；
- `MainWindow.open_paths()` / `_open_path()`：现有异步打开和 Dataset Tab 生命周期；
- `SasDatasetReader`：SAS7BDAT/XPT 读取；
- `TempManager`：临时副本和 cache 生命周期；
- `AppSettings.load()/save()`：普通用户设置 JSON 的原子保存；
- SQLite：可为多窗口共享的 Workspace Favorites 提供事务化增量保存；
- `QThreadPool + Worker`：后台任务和错误回调；
- 左侧 `Variables` Dock：现有侧栏布局参考。

目前 `AppSettings` 只保存 `last_open_directory`，没有收藏文件夹模型；`MainWindow` 也没有目录浏览或防止同一 source 重复打开的统一入口。由于已确认 Mini File Manager 可以启动多个 Viewer 窗口，收藏不能作为整个 `settings.json` 的一个字段反复覆盖保存。

## 3. 推荐 UI

### 3.1 File 菜单

建议调整为：

```text
File
├── Open Dataset(s)…                 Ctrl+O
├── Open Folder as Workspace…        Ctrl+Shift+O
├── Favorite Workspaces              >
│   ├── ADaM Production
│   ├── SDTM Transfer
│   └── Manage Favorites…
├── Reload
...
```

现有 `Open` QAction 的显示名可以改为 `Open Dataset(s)…`，行为保持不变。

### 3.2 工具栏 Open 按钮

为满足“在 Open 那边增加入口”，建议将工具栏 Open 改为 `MenuButtonPopup`：

- 点击按钮主体：继续执行 `Open Dataset(s)…`；
- 点击右侧下拉箭头：显示 `Open Dataset(s)…`、`Open Folder as Workspace…` 和收藏列表；
- 现有 `Ctrl+O` 行为保持不变。

不要让单击 Open 的原有行为突然变成选择文件夹。

### 3.3 Folder Panel

建议新增左侧 `QDockWidget("Folder Panel")`，完全参考现有 Variables Panel 的侧栏模式，而不是创建中央 Dataset Tab。

整体布局固定为：

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Toolbar                                                              │
├──────────────┬────────────────┬──────────────────────────────────────┤
│ Folder Panel  │ Variables      │ 当前 Dataset Tab / Table            │
│              │ Panel          │ 始终是中央主要内容                  │
│              │                │                                      │
│       ↔      │        ↔       │                                      │
└──────────────┴────────────────┴──────────────────────────────────────┘
```

Folder Panel 与 Variables Dock 同时打开时采用水平排列，顺序固定为：

```text
Folder Panel → Variables Panel → Dataset
```

Folder Panel 是最左侧 Dock，Variables Panel 位于它右侧，中央区域继续显示正常 Dataset Tab。两个 Dock 之间以及 Variables Panel 与 Dataset 之间都必须有可拖动的 splitter/dock separator，用户可以分别调整 Panel 宽度。

实现建议使用两个独立 `QDockWidget`，通过 `splitDockWidget(folder_dock, variables_dock, Qt.Horizontal)` 建立次序；不要 tabify 两个 Dock，也不要把两个 Panel 塞进一个固定宽度的复合 widget。

默认建议：

- Folder Panel：约 250 px；
- Variables Panel：约 285 px，保持接近当前默认；
- 两个 Panel 都只设置合理 minimum width，不设置 fixed width；
- Dataset 中央区域应保留可用最小宽度；
- 用户拖动后的宽度应在本次会话持续有效；
- 推荐把 Dock layout/width/visibility 保存到用户设置，在下次启动恢复；恢复失败时退回默认布局。

`View` 菜单增加独立的 checkable action：

```text
View
├── Folder Panel             ✓
├── Variables Panel         ✓
...
```

显示/隐藏规则：

- 关闭或隐藏 Folder Panel 只隐藏侧栏，不关闭 Workspace；
- 再次显示时保留当前 Workspace、文件列表、搜索文字和收藏状态；
- 隐藏 Folder Panel 不关闭任何 Dataset Tab；
- 关闭 Dataset Tab 不隐藏或清空 Folder Panel；
- 切换 Dataset Tab 不自动改变 Folder Panel 当前文件夹；
- `Folder Panel` QAction 的 checked 状态与 Dock 实际 visibility 双向同步；
- `Variables Panel` 和 `Folder Panel` 各自独立显示/隐藏，不互相强制切换；
- 两者都显示时必须保持 `Folder → Variables → Dataset` 的从左到右顺序；
- 隐藏其中一个 Panel 后，剩余 Panel 和 Dataset 自动取得释放的宽度；
- 重新显示 Panel 时恢复上次合理宽度，不覆盖用户对另一个 Panel 的调整；
- 用户可以拖动每条 Dock separator，不能因为内部控件的 minimum width 导致无法缩窄；
- Workspace 扫描进行中时隐藏 Panel，后台任务可以完成，但不得强制重新弹出 Panel；
- 用户再次选择 `Open Folder as Workspace…` 时应显示 Folder Panel，并把它置于 Variables Panel 前面。

实现时优先复用 Variables Panel 已有的背景色、边距、搜索框、折叠/显示行为和 View action wiring，不建立另一套视觉风格。

建议布局：

```text
Folder Panel

[ Workspace: ADaM Production       ▾ ] [★] […]
[ Search files…                         ] [Refresh]

adsl.sas7bdat
adae.sas7bdat
adlb.sas7bdat

3 datasets | /project/adam
```

文件区域使用单列 `QListView` 或等价单列控件，每个条目只显示完整文件名和扩展名，例如：

```text
adlb.sas7bdat
transport.xpt
```

不要增加 `Type`、`Size`、`Modified`、`Status` 等列。完整绝对路径仅放在 tooltip 和 `Copy Full Path` 中，不占用 Panel 可见宽度。

交互：

- 单击：选择文件；
- `Ctrl/Shift`：多选；
- 双击或 Enter：打开选择的数据集；
- 右键：`Open`、`Open Containing Folder`、`Copy Full Path`；
- 星标按钮：收藏/取消收藏当前 Workspace；
- Workspace 下拉框：显示当前 Workspace 和已收藏 Workspace；
- Search：只按显示文件名过滤 Folder Panel，不改变磁盘内容；
- Refresh：重新扫描目录；
- `…`：Browse、Remove from Favorites、Close Workspace。

收藏 Workspace 时允许用户自定义显示名称。默认名称使用文件夹 basename；用户可以在收藏时修改，也可以之后通过 `Rename Favorite…` 重命名。

```text
Add Favorite Workspace

Name
[ ADaM Production                    ]

Folder
C:\clinical\project\adam

[ Cancel ] [ Add Favorite ]
```

重命名只修改收藏的 `label`，绝不重命名、移动或修改磁盘文件夹。名称规则：

- trim 首尾空白；
- 空名称回退到文件夹 basename；
- 建议最大 80 个字符；
- 允许中文和常规 Unicode；
- 不要求名称唯一，因为真实身份由规范化路径决定；
- 同名收藏在 tooltip 中显示完整路径以便区分；
- 同一路径再次收藏时更新现有 label，不能创建重复收藏。

### 3.4 空状态和错误状态

没有打开 Workspace：

```text
Open a folder to view its SAS datasets.
[ Open Folder as Workspace… ]
```

目录中无匹配文件：

```text
No SAS datasets were found in this folder.
```

收藏目录不存在、网络盘未连接或无权限：

```text
This workspace folder is unavailable.
Reconnect the drive or choose another folder.
[ Retry ] [ Choose Folder… ] [ Remove Favorite ]
```

不可用收藏不要自动删除，避免移动盘暂时离线时丢失用户配置。

## 4. v1 行为规则

### 4.1 Workspace 与 Dataset Tab 相互独立

- 切换 Workspace 不关闭已打开的 Dataset Tab；
- Close Workspace 只清空 Folder Panel，不关闭 Dataset Tab；
- 关闭 Dataset Tab 不清空 Workspace；
- Workspace 文件列表不应用 Dataset Tab 的 WHERE、排序或 Column selection；
- 收藏 Workspace 不保存当前打开的 Tab、WHERE、Filter History、Builder 配置或分析 Result。

这能避免“收藏工作区”被误解成完整 session restore。

### 4.2 文件发现规则

推荐 v1：

1. 只扫描用户选择文件夹的**第一层**；
2. 只返回普通文件；
3. suffix 使用 case-insensitive 判断；
4. 按文件名 `casefold()` 升序稳定排序；
5. 不读取 SAS header 和 metadata；
6. 不自动打开任何文件；
7. Refresh 前后的异步结果用 generation id 隔离，过期扫描结果不能覆盖新 Workspace。

v1 已确认不递归扫描子目录。临床传输目录可能很大，递归扫描网络盘会产生不可预测的等待和重复数据集名称。未来如有需要，可以增加明确的 `Include subfolders` 选项，但不能静默改变 v1 行为。

### 4.3 SAS7BDAT 与 XPT

v1 已确认同时显示 `.sas7bdat` 和 `.xpt`，标题和空状态统一称为 `SAS datasets`。文件类型通过名称中的扩展名自然体现，不增加单独 Type 列；两种 suffix 都必须 case-insensitive。

这样同一文件夹内的 XPT 不会被隐藏，而且两类文件最终继续走同一个 `open_paths()`。

### 4.4 重复打开

Workspace 双击文件时应先规范化绝对路径：

- 若同一 source 已在 `LoadingPage`：聚焦 Loading Tab；
- 若同一 source 已在 `DatasetTab`：聚焦已有 Tab；
- 否则调用现有 `_open_path()`；
- Windows 路径比较使用 case-insensitive key；macOS/Linux 使用规范化绝对路径。

建议把查找逻辑放在一个统一的 `open_or_focus_path()`，但第一阶段只供 Workspace 使用，避免未经评审改变现有 File Open 的重复打开行为。以后可以再统一两个入口。

### 4.5 收藏和启动行为

收藏只保存文件夹路径和可选显示名，不保存文件清单。Favorites 使用独立的：

```text
<application data>/ClinicalDataViewer/workspace_favorites.sqlite
```

建议 schema：

```sql
create table workspace_favorites (
    path_key   text primary key,
    path       text not null,
    label      text not null,
    updated_at text not null
);
```

其中 `path_key` 是平台相关的规范化路径 key，仅用于去重；`path` 保留可再次打开的原路径；`label` 是用户自定义显示名称。

每种操作都只修改对应记录：

```sql
-- Add or update the same folder
insert into workspace_favorites(path_key, path, label, updated_at)
values (?, ?, ?, ?)
on conflict(path_key) do update set
    path = excluded.path,
    label = excluded.label,
    updated_at = excluded.updated_at;

-- Rename
update workspace_favorites
set label = ?, updated_at = ?
where path_key = ?;

-- Remove
delete from workspace_favorites
where path_key = ?;
```

这是真正的增量保存，不会为了增加一个收藏而重写整个 JSON 或整个收藏集合。

规则：

- 路径规范化后去重；Windows 下大小写不敏感；
- 用户可以自定义收藏显示名称，默认值为文件夹 basename；
- `Rename Favorite…` 只更新 label，不修改 folder path；
- 建议最多保存 50 个收藏；
- SQLite 使用 WAL、busy timeout 和短事务，允许多个 Viewer 进程安全增删改；
- 读取时忽略无效记录并记录诊断，但不因目录当前不存在而删除；
- 增加/重命名/删除后重新查询 Favorites，不能依赖启动时的旧内存快照；
- Favorite menu 打开或 Folder Panel 重新获得焦点时刷新列表，以看到其他 Viewer 的修改；
- 程序启动时加载收藏列表，但**不自动扫描、不自动打开上次 Workspace**；
- 用户从 File 菜单或 Workspace 下拉框选择后才扫描。

不自动恢复上次目录是为了避免程序启动时被离线网络盘拖慢。若用户需要“启动时恢复上次 Workspace”，建议以后作为独立设置。

## 5. 推荐架构

不要继续把目录扫描和收藏逻辑直接堆进 `MainWindow`。

### 5.0 施工架构红线

本功能必须采用**增量接入、保持既有行为**的方式实施。以下两条属于施工红线，不是可选建议：

1. **不要修改之前的业务逻辑。** Workspace 只负责发现文件、展示文件夹、管理收藏并把用户明确选择的路径交给现有数据集打开入口。现有 Dataset Open、Reader、临时副本、SQLite cache、WHERE、排序、Variables Panel、Dataset Tab、Reload、Export、Compare、Merge、Analysis Builder、文件关联和生命周期逻辑必须继续沿用，不能为了实现 Workspace 顺手改写、合并或重新定义。若实施中发现必须改变既有行为，应停止该部分施工，单独记录原因、影响范围和迁移方案，获得确认后再处理。
2. **不要在 `MainWindow` 中插入大量业务逻辑。** `MainWindow` 只允许增加菜单/工具栏/Dock 的创建与连接、Controller 初始化，以及少量明确的 host adapter 方法。目录扫描、路径规范化、后缀过滤、Workspace 状态、异步 generation guard、Favorites CRUD、SQLite transaction、收藏数量限制、错误分类和列表状态转换必须分别留在 `workspace/`、`WorkspaceController` 或 `WorkspacePanel` 中，不能以内联 helper 或大段 slot 的形式回流到 `ui/main_window.py`。

推荐依赖方向固定为：

```text
MainWindow (UI composition and routing only)
    ↓ host contract / Qt signals
WorkspaceController (workflow and runtime state)
    ├── WorkspaceScanner (filesystem discovery)
    ├── WorkspaceFavoritesStore (persistent CRUD)
    └── WorkspacePanel (presentation and user interaction)
            ↓ selected paths
MainWindow.open_or_focus_paths()
            ↓
existing dataset open/read/cache workflow
```

施工过程中不得让 `WorkspaceController` 直接访问 `MainWindow` 的任意私有字段；应通过最小、命名清晰的 host protocol/adapter 调用现有能力。也不得让 `WorkspacePanel` 直接读取目录、打开数据集或写入 Favorites 数据库。

### 5.1 新增文件

```text
clinical_data_viewer/
├── workspace/
│   ├── __init__.py
│   ├── models.py
│   ├── favorites_store.py
│   └── scanner.py
├── controllers/
│   └── workspace_controller.py
└── ui/
    └── workspace_panel.py
```

职责：

#### `workspace/models.py`

```python
WorkspaceFavorite(path, label)
WorkspaceEntry(path, name)
WorkspaceSnapshot(root, entries)
```

这些对象只描述磁盘条目，不持有 `DatasetHandle`、SQLite connection 或 SAS metadata。

#### `workspace/scanner.py`

提供纯逻辑入口：

```python
scan_workspace(root: Path, allowed_suffixes: frozenset[str]) -> WorkspaceSnapshot
```

要求：

- 使用 `os.scandir()` 或等价的单层扫描；
- suffix case-insensitive；
- 单个文件 stat 失败时跳过该条目并记录 warning；
- 根目录不存在、不是目录或不可读时返回明确异常；
- 不导入 Qt，便于独立测试；
- 不打开数据文件。

#### `workspace/favorites_store.py`

提供独立、无 Qt 的持久化 API：

```python
list_favorites() -> tuple[WorkspaceFavorite, ...]
upsert_favorite(path: Path, label: str) -> WorkspaceFavorite
rename_favorite(path_key: str, label: str) -> None
remove_favorite(path_key: str) -> None
```

要求：

- 数据库位于 `application_data_directory()`；
- 初始化 schema 使用 `create table if not exists`；
- WAL + busy timeout；
- 每次写操作使用短事务并及时关闭 connection；
- 同一路径使用 `UPSERT`，不产生重复行；
- 最大 50 条的检查与插入放在同一个 `BEGIN IMMEDIATE` transaction；
- 不因为 path 当前离线而删除记录；
- SQLite 错误保留原始异常，UI 提示不能静默假装保存成功。

#### `controllers/workspace_controller.py`

负责：

- Browse folder；
- 启动现有 Worker 后台扫描；
- scan generation / stale callback 防护；
- 当前 Workspace runtime state；
- 通过 `WorkspaceFavoritesStore` 增量收藏、取消收藏和持久化；
- 新增收藏、编辑显示名称和 `Rename Favorite…`；
- 将用户选择的文件交给 MainWindow host 的 `open_or_focus_paths()`；
- 不直接读取 SAS 数据。

#### `ui/workspace_panel.py`

只负责 Folder Panel 控件、选择、搜索、按钮状态和信号，不访问磁盘、不保存设置、不调用 reader。

### 5.2 修改文件

#### `settings.py`

新增：

```python
last_workspace_directory: str
```

`settings.json` 只继续保存普通 UI setting，例如最后一次 Browse 的父目录。不要在 AppSettings 中保存 `favorite_workspaces`；Favorites 的 normalization、去重、数量限制和多进程事务全部由独立 Store 负责。

#### `ui/main_window.py`

只增加：

- Actions/Menu/toolbar split button；
- Workspace Dock 和 Controller 初始化；
- host 方法：打开或聚焦路径、查询当前 open/loading source；
- 提供查询当前 open/loading source 的 host 方法，用于防止重复打开，但不在 Folder Panel 显示状态。

目录扫描、收藏 SQLite 操作和文件过滤不应放在 MainWindow。

#### `app.py`

只在需要时补充 Folder Panel 的 object-name 样式；不要改变 Reader 或启动参数逻辑。

## 6. 后台扫描与性能

虽然 v1 不递归，网络目录或含大量文件的目录仍可能阻塞 UI，因此扫描必须在 Worker 中完成。

推荐流程：

```text
User selects folder A
    ↓ generation = 10
Worker scans A
    ↓
User quickly selects folder B
    ↓ generation = 11
Worker A finishes → generation mismatch → discard
Worker B finishes → render snapshot
```

Folder Panel 只接收一次 immutable snapshot。搜索由 proxy/filter model 在内存完成，不重复访问磁盘。

v1 不建议使用持续 `QFileSystemWatcher`：网络盘、批量传输目录和移动盘可能产生大量事件或 watcher 限制。提供明确 Refresh，并在重新选择收藏时自动扫描，行为更可预测。未来可增加可选自动刷新。

## 6.1 外部程序打开 Workspace

未来 Mini File Manager 可以提供：

```text
Open as SAS Viewer Workspace
```

推荐通过稳定的 command-line contract 调用 Viewer，而不是让 Mini File Manager 访问 Viewer 的设置文件、Qt widget 或内部 Python module：

```text
SASDataViewer.exe --workspace "C:\clinical\project\adam"
```

macOS/Linux 开发环境等价为：

```text
sasdataviewer --workspace "/project/adam"
```

SASDataViewer 负责：

1. 解析 `--workspace` 后的单个文件夹路径；
2. 展开用户目录并规范化为绝对路径；
3. 验证路径存在且为可读文件夹；
4. 正常创建主窗口；
5. UI 初始化完成后打开 Folder Panel；
6. 在后台扫描第一层 `.sas7bdat` 和 `.xpt`；
7. 不自动打开其中的数据集；
8. 路径无效时保留主窗口并显示可操作错误。

外部打开不要求该文件夹以前在 SASDataViewer 中出现过，也不要求它已经被收藏。Mini File Manager 可以把任意当前选择的可读文件夹作为 `--workspace PATH` 传入：

```text
Previously unknown folder
    ↓ Mini File Manager: Open as SAS Viewer Workspace
SASDataViewer --workspace PATH
    ↓ validate + scan
Folder Panel displays the folder
```

首次外部打开的文件夹作为当前临时 Workspace：

- 正常显示第一层 `.sas7bdat/.xpt`；
- 即使没有匹配文件，也显示空 Workspace，而不是把它判为无效；
- 不自动写入 Favorites，避免 Mini File Manager 每次查看临时目录都污染收藏列表；
- 用户可在 Folder Panel 中点星标，输入自定义名称后收藏；
- 收藏后才增量写入 `workspace_favorites.sqlite`；
- 未收藏就关闭 Workspace/应用时，不留下 favorite 记录；
- 文件夹不存在、不是目录或无读取权限时才显示 unavailable/error。

Mini File Manager 必须以参数数组启动进程，不要拼接 shell command。例如概念上使用：

```python
[viewer_executable, "--workspace", selected_folder]
```

这样可以正确处理空格、中文、单引号、`#` 和 UNC path，并避免 shell quoting/injection 问题。

### 启动参数兼容规则

当前 Viewer 已支持通过命令行或 Windows 文件关联传入 `.sas7bdat/.xpt` 文件。增加 Workspace 参数时必须保留该行为。

建议把启动参数解析结果整理为明确模型：

```python
StartupRequest(
    workspace_path: Path | None,
    dataset_paths: tuple[Path, ...],
)
```

规则：

- 没有 `--workspace`：保持现有文件关联行为；
- 只有 `--workspace FOLDER`：打开 Folder Panel，不自动打开文件；
- 同时有 Workspace 和显式 dataset paths：先显示 Workspace，再打开显式指定的数据集；
- 重复 `--workspace`：作为参数错误处理，不静默猜测；
- 不把普通未知目录参数自动当 Workspace，必须使用显式 flag；
- `--workspace` 后缺少值时显示清楚错误，不崩溃；
- Mini File Manager 的菜单文字统一使用 `Workspace`，不要使用 `Workplace`。

### 新进程与已有进程

当前 SASDataViewer 没有 single-instance IPC。v1 已确认：Mini File Manager 每次外部调用可以启动一个新的 Viewer 窗口并打开对应 Folder Panel。这是最小、安全且容易验收的方案，现有 Viewer 窗口和其中打开的数据集不会被打断。

如果未来需求改变为“Viewer 已经打开时，复用现有窗口并跳转到 Folder Panel”，则作为独立第二阶段增加：

```text
new process
    ↓ QLocalSocket
existing SASDataViewer QLocalServer
    ↓ validate workspace request
raise/activate existing window
    ↓
open requested Folder Panel
```

这不应和 Workspace v1 强行绑在一起，因为 single-instance 会同时影响现有文件关联、多窗口行为、进程崩溃恢复和 Windows 打包验收。

已确认实施顺序：

1. Workspace v1 同时提供 `--workspace` contract；
2. Mini File Manager 调用它并启动新窗口；
3. v1 不实现 single-instance IPC，也不把请求转发到已有窗口。

不推荐 v1 使用 `sasdataviewer://workspace?...` 自定义 URL protocol。它需要额外的 Windows registry、URL encoding 和安装器配置，而 command-line flag 已足够满足 Mini File Manager 集成。

## 7. 安全与数据边界

- Workspace 扫描只读，不创建、重命名或删除源文件；
- 收藏只把文件夹路径和 label 写入本机 `workspace_favorites.sqlite`；
- 不保存文件内容、SAS metadata、用户凭据或网络盘密码；
- Folder Panel 不绕过现有 TempManager，真正打开数据集仍复制到 session temp；
- 不长时间持有源 SAS 文件句柄；
- `Open Containing Folder` 属于外部系统动作，需要单独处理 Windows/macOS/Linux，并可以留到 v1.1；
- symlink/junction 必须作为边界明确测试。推荐 v1 允许指向普通文件的 symlink，但不跟随目录 symlink递归。

## 8. Concerns 与建议

| Concern | 影响 | 建议 |
|---|---|---|
| `Workspace` 与 `Workplace` 名称 | UI 和文档不一致 | UI 固定使用 `Workspace` |
| 是否显示 XPT | 已支持的文件可能在 Folder Panel 中消失 | 已确认同时显示 SAS7BDAT/XPT |
| 是否递归 | 大目录、网络盘扫描慢；同名文件难区分 | v1 仅第一层，未来显式开关 |
| 收藏是否等于 session restore | 用户可能期待恢复 Tab/WHERE/结果 | v1 明确只收藏 folder path |
| 网络盘/移动盘离线 | 启动或切换时卡顿、误删收藏 | 不自动扫描；后台扫描；保留 unavailable favorite |
| 超大目录 | UI 卡顿、旧结果覆盖新目录 | Worker + immutable snapshot + generation id |
| 同一文件重复打开 | 重复 cache 和 Tab，增加内存/磁盘占用 | Workspace 路径先 focus existing/loading tab |
| 文件在扫描后被删除 | 双击时打开失败 | 复用现有错误框，同时 Refresh 当前 Workspace |
| 文件变化自动刷新 | watcher 在传输目录上噪声大 | v1 手动 Refresh，不启用 watcher |
| 收藏路径隐私 | Favorites SQLite 会留下项目目录 | 只本机保存；提供 Remove Favorite；不写入日志/导出 |
| 多 Viewer 同时修改收藏 | JSON 整体覆盖会丢失其他进程更新 | 使用独立 SQLite、WAL、busy timeout 和逐行事务 |
| 其他进程更新后的 UI | 当前窗口的内存列表可能过期 | mutation 后、菜单打开时和 Panel focus 时重新查询 Store |
| 自定义名称重名 | 两个 Workspace 可能显示相同 label | 允许重名，以规范化 path 为身份，并在 tooltip 显示完整路径 |
| 两个左侧 Dock 同时显示 | 小窗口下可能压缩数据表 | 保持 Folder→Variables→Dataset 三栏，允许拖动宽度，并设置合理最小值 |
| Panel 内部 minimum width | 搜索框或文件列表可能阻止用户缩窄 Dock | 避免 fixed width，检查所有 child size policy 和 horizontal scrollbar |
| 文件信息过多 | Type/Size/Modified/Status 会占用窄 Panel | 单列只显示完整文件名，完整路径放 tooltip |
| Dock 布局恢复 | 保存的旧尺寸可能超出新屏幕 | 恢复后 clamp 到当前窗口可用宽度，失败时使用默认宽度 |
| Panel 隐藏后的状态 | 隐藏时清空会让用户误以为 Workspace 被关闭 | Dock visibility 与 Workspace lifecycle 分离，重新显示恢复原状态 |
| 中央内容被替换 | Folder Panel 做成中央 Tab 会影响数据浏览和分析操作 | Folder Panel 固定为可隐藏侧栏，Dataset Tab 始终留在中央 |
| Mini File Manager 集成 | 两个程序直接共享内部代码会形成强耦合 | 使用稳定的 `--workspace PATH` command-line contract |
| 已有 Viewer 实例 | 外部调用会打开新窗口 | 已确认接受；v1 不实现 QLocalServer/QLocalSocket |
| 外部路径参数 | 空格、中文和 UNC path 容易被错误拆分 | 使用 argv list，不经 shell；Viewer 内再次验证路径 |
| 当前工作区有未提交修改 | 实施时容易覆盖其他 Builder 工作 | 实施前记录 git status，只改文档列出的文件 |

我的总体建议是：先实现“单层、只读、手动刷新、收藏路径、双击打开”的小而稳定版本，不要第一版就加入递归、自动 watcher、session restore 或复杂项目文件。

## 9. 分阶段施工计划

### Phase 0：行为边界和回归基线

开始写功能代码前：

- 记录 `git status`，保护用户已有修改；
- 找出现有 `open_files()`、`open_paths()`、`_open_path()`、Reader、TempManager 和 Dataset Tab 生命周期入口；
- 为这些现有入口建立或确认回归测试基线；
- 明确 `MainWindow` 只负责 composition/wiring，先定义 Workspace host contract，再实现 Controller；
- 不移动、重构或改名与 Workspace 无关的现有业务流程。

完成标准：现有 dataset open/file association/reader/cache 测试在 Workspace 代码加入前通过，并形成后续回归对照。

### Phase 1：Workspace domain 和 scanner

涉及：

- 新增 `workspace/models.py`；
- 新增 `workspace/scanner.py`；
- 明确 allowed suffix；
- 实现规范化路径、单层扫描、稳定排序和明确错误。

完成标准：纯 Python 测试通过，不修改 UI。

### Phase 2：收藏持久化

涉及：

- 新增 `WorkspaceFavoritesStore` 和独立 SQLite schema；
- 普通 `AppSettings` 只增加 `last_workspace_directory`；
- 使用 UPSERT/UPDATE/DELETE 增量保存；
- 使用 WAL、busy timeout 和短事务；
- 规范化 path key、去重、最多 50 条；
- 保留暂时不存在的收藏；
- 多窗口写入不能互相覆盖。

完成标准：旧 settings 正常加载；收藏 CRUD/round-trip 稳定；两个独立 Store connection 交错操作不会丢数据。

### Phase 3：Folder Panel UI

涉及：

- 新增 `WorkspacePanel`；
- Workspace selector、star、search、refresh、单列文件名列表、空状态；
- 多选、双击、Enter、按钮 enable/disable；
- 与 Variables Dock 水平排列为 `Folder → Variables → Dataset`；
- 两个 Dock 独立可见，并通过分隔线调整宽度；
- 设定合理默认/最小宽度，不使用 fixed width；
- 保存并安全恢复 Dock visibility、顺序和宽度；
- 增加 `View > Folder Panel` checkable action，并与 Dock visibility 双向同步；
- 隐藏/重新显示时保留 Workspace snapshot、search 和 favorite 状态；
- 浅蓝白主题保持一致。

完成标准：离线 UI tests 可覆盖状态变化，不访问真实 SAS 文件。

### Phase 4：Workspace Controller 和 MainWindow wiring

涉及：

- 新增 `WorkspaceController`；
- 后台扫描和 generation guard；
- File/toolbar actions；
- Favorite submenu；
- `open_or_focus_paths()`；
- open/loading path 查询，用于聚焦已有 Tab，但不渲染状态列。

限制：`MainWindow` 本阶段只增加 QAction/Dock wiring、Controller 初始化和最小 host adapter；禁止在 `MainWindow` 中实现扫描、收藏、路径过滤、SQLite 或 Workspace 状态机。

完成标准：从 Workspace 打开文件继续走现有 reader/cache；快速切换目录不会显示旧结果。

### Phase 4A：外部启动 contract

涉及：

- 在 `app.py` 增加结构化 startup argument parsing；
- 保留现有 dataset file-association paths；
- 新增单个 `--workspace PATH`；
- Qt 主窗口显示后通过 Controller 打开 Folder Panel；
- 无效目录显示英文错误；
- 不实现 single-instance IPC。

完成标准：Mini File Manager 可以通过 executable + argv 直接打开指定 Folder Panel，且不影响双击 `.sas7bdat/.xpt` 的现有行为。

### Phase 5：错误和生命周期验收

涉及：

- 不存在、无权限、网络盘断开；
- 文件扫描后被删除；
- Workspace 关闭/切换；
- 应用关闭时仍有扫描 worker；
- 收藏目录恢复；
- 多文件同时打开。

完成标准：没有 UI 卡死、崩溃、源文件写入或临时目录泄漏。

## 10. 测试计划

### Scanner tests

1. 只返回允许的 suffix；
2. suffix 大小写混合；
3. 非数据文件和子目录被忽略；
4. v1 不递归子目录；
5. 空格、中文、单引号、`#` 路径；
6. 稳定 case-insensitive 排序；
7. 空文件夹；
8. 根路径不存在；
9. 根路径是文件而非目录；
10. 无权限目录；
11. 单个 entry 在 stat 时消失；
12. symlink/junction 规则。

### Favorites Store / Settings tests

1. 收藏 SQLite CRUD 和 round-trip；
2. 普通旧 settings 无新字段仍可加载；
3. malformed row/path；
4. duplicate path UPSERT；
5. Windows path case normalization；
6. 不存在收藏仍保留；
7. 最大数量限制在 transaction 内生效；
8. label 为空时使用 folder name；
9. 自定义 Unicode label round-trip；
10. 同一路径再次收藏只更新 label；
11. Rename 不改变 path；
12. 两个 connection 交错 add/rename/remove 不丢数据；
13. busy timeout 后错误可诊断；
14. 一个 Viewer 的旧内存 snapshot 不会覆盖另一个 Viewer 的新记录；
15. Store 不重写 `settings.json`。

### Controller tests

1. Browse 成功后启动扫描；
2. scan error 显示 unavailable state；
3. A→B 快速切换时忽略 A 的迟到结果；
4. Refresh 保持当前搜索和收藏状态；
5. 收藏/取消收藏即时保存；
6. 收藏时可以自定义名称，空名称回退到 folder basename；
7. `Rename Favorite…` 只更新 label，并在重启后保留；
8. 收藏目录下次启动可选择；
9. double-click 调用现有 open path；
10. 多选一次打开多个文件；
11. 已打开/正在加载的文件只聚焦、不重复打开；
12. 切换/关闭 Workspace 不关闭 Dataset Tab。

### Startup integration tests

1. `--workspace <folder>` 被解析为 Workspace request；
2. 空格、中文、单引号、`#` 路径；
3. Windows drive path 和 UNC path；
4. `--workspace` 缺少值；
5. 重复 `--workspace`；
6. 无效路径和普通文件路径被拒绝；
7. Workspace request 不自动打开目录中的数据集；
8. Workspace 与显式 dataset paths 可同时存在；
9. 原有 `.sas7bdat/.xpt` 文件关联参数测试保持通过；
10. Mini File Manager 使用 argv list 的集成 smoke test。
11. 从未打开、从未收藏的文件夹可以通过 `--workspace` 打开；
12. 首次外部打开不会自动加入 Favorites；
13. 首次打开后点击星标可以自定义名称并持久化；
14. 没有 SAS 数据集的有效文件夹显示 empty state，而不是 path error。

### UI tests

1. File 菜单和 Open 下拉入口；
2. `Ctrl+O` 仍打开 dataset file dialog；
3. `Ctrl+Shift+O` 打开 folder dialog；
4. Folder Panel 与 Variables Panel 可同时显示并可独立隐藏；
5. empty/loading/ready/unavailable 状态；
6. Search filter；
7. star、Refresh、Open 按钮 enabled/disabled 状态；
8. 全路径 tooltip；
9. 长文件名和窄侧栏可读；
10. 键盘 Enter 和多选。
11. `View > Folder Panel` 可以独立显示/隐藏；
12. Dock visibility 与 action checked 状态同步；
13. 隐藏后重新显示仍保留当前 Workspace 和搜索条件；
14. 隐藏 Folder Panel 不关闭 Dataset Tab；
15. Dataset Tab 切换/关闭不清空 Folder Panel；
16. Folder Panel 与 Variables Panel 同时显示时顺序为 `Folder → Variables → Dataset`；
17. `Open Folder as Workspace…` 会显示并 raise Folder Panel。
18. Folder/Variables 两条分隔线都可以拖动；
19. 调整一个 Panel 宽度不会锁死另一个 Panel；
20. 隐藏任一 Panel 后中央 Dataset 自动扩展；
21. 再次显示时恢复合理宽度；
22. 窄窗口和高 DPI 下仍能看到 Folder 文件列表及 Variables 内容；
23. child widget 不会因 fixed/minimum width 阻止 Dock 缩窄；
24. 保存的异常超宽布局在重启后会被 clamp，不把 Dataset 挤出可视区域。
25. 文件列表只显示文件名，不显示 Type/Size/Modified/Status；
26. 文件名保留 `.sas7bdat` 或 `.xpt` 扩展名；
27. 完整路径只通过 tooltip/Copy Full Path 提供。

### Regression

- 现有 File Open 多选；
- Windows 文件关联/command-line path；
- XPT/SAS7BDAT initial/background cache；
- Dataset close/reload；
- Compare/Merge source lists；
- 所有 Builder source selector；
- Variables panel；
- TempManager cleanup；
- 全量 unittest；
- changed-file Ruff；
- `git diff --check`；
- Windows 打包版真实本地目录、UNC path 和离线盘验收。
- Workspace 打开文件必须调用既有 dataset open/read/cache 流程，不能出现第二套 Reader 或 Dataset Tab 生命周期；
- 现有 Open Dataset(s)、命令行单文件打开和 Windows 文件关联的行为及错误提示保持不变；
- 对 `MainWindow` 的结构测试/代码审查确认其只包含 Workspace composition、signal wiring 和 host adapter，不包含扫描、Favorites 持久化或状态机业务实现；
- Workspace 相关测试分别落在 scanner、favorites store、controller 和 panel 边界，不能只依赖 MainWindow 集成测试覆盖业务逻辑。

## 11. Acceptance Criteria

1. File 和 toolbar Open 附近存在 `Open Folder as Workspace…`；
2. 用户选择文件夹后出现 Folder Panel；
3. Folder Panel 不阻塞主 UI；
4. 只列出约定范围内的数据文件；
5. 双击/Enter/按钮可以打开数据集；
6. 多选可以一次打开多个；
7. 文件仍使用现有 reader、temp copy 和 Dataset Tab；
8. Folder Panel 不自动读取 SAS metadata；
9. Workspace 切换不关闭已打开 Dataset Tab；
10. Dataset Tab 关闭不清空 Workspace；
11. 同一文件从 Workspace 重复打开时聚焦已有 Tab；
12. Workspace 可收藏和取消收藏；
13. 收藏在程序重启后仍存在；
14. 收藏名称可以由用户自定义，并可在之后重命名；
15. 不可用收藏保留并显示可操作错误；
16. Refresh 可重新发现新增/删除文件；
17. 搜索仅过滤列表；
18. 路径含空格和非 ASCII 字符可用；
19. 不修改、锁定或删除源数据；
20. 不影响现有 Open、文件关联、Compare、Merge 和 Builder；
21. Windows 打包版完成真实目录和网络路径验收；
22. `--workspace PATH` 可以从外部程序打开指定 Folder Panel；
23. 外部启动不破坏现有 dataset file association；
24. v1 明确允许启动新 Viewer 窗口，不隐含 single-instance 承诺；
25. Folder Panel 是可隐藏的 Dock Panel，不替换中央 Dataset Tab；
26. View 菜单可以控制 Folder Panel，并且隐藏后保留 Workspace 状态；
27. Folder Panel 与 Variables Panel 可以同时显示，中央数据集内容保持不变；
28. 两者同时显示时排列为 `Folder Panel | Variables Panel | Dataset`；
29. 两个 Panel 的宽度均可由用户拖动调整，并可独立隐藏；
30. 隐藏或调整 Panel 不清空 Workspace、Variables 状态或 Dataset Tab；
31. Folder Panel 的数据集列表只显示完整文件名，例如 `adlb.sas7bdat`。
32. 从未在 Viewer 中打开或收藏的文件夹也可以由 Mini File Manager 打开；
33. 外部首次打开不自动收藏，只有用户明确点击星标后才持久化。
34. Workspace 实现不改变现有 Dataset Open、Reader/cache、WHERE、Variables、Reload、Export、Compare、Merge 或 Analysis Builder 的业务语义；
35. `MainWindow` 只承担 Workspace 的 UI composition、路由和最小 host adapter，不包含目录扫描、Favorites SQLite、文件过滤或 Workspace 状态机的大段业务代码；
36. Workspace 选中的数据集始终进入既有 `open_or_focus_paths()` 和 dataset 生命周期，不创建第二套打开流程；
37. 若实现需要改变既有业务 contract，该变化必须作为独立需求评审，不能混入 Workspace 施工。

## 12. 已确认决策与剩余评审点

已确认：

1. Folder Panel 同时显示 `.sas7bdat + .xpt`；
2. v1 只显示所选文件夹第一层，不递归子目录。

推荐直接采用的 v1 默认：

- 收藏只保存路径和可选 label；
- 不自动恢复完整 session；
- 程序启动时不自动扫描收藏目录；
- 使用手动 Refresh；
- 提供 `--workspace PATH` 给 Mini File Manager；
- v1 外部调用启动新的 SASDataViewer 窗口；
- 不复用已运行窗口，不实现 single-instance IPC。

当前 Workspace v1 的关键交互决策已全部确认，可以按本文分阶段施工。
