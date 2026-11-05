# P21 Issue Explorer 架构设计

状态：第一版设计基线已确认，尚未开始实现

更新日期：2026-11-05

## 1. 文档目的

本文定义 P21 Issue Explorer 与 SAS Viewer 的边界、进程通信、数据快照、结果视图和打包方式。

相关文档：

- [第一版需求](p21-issue-explorer-requirements.md)
- [匹配规范](p21-issue-matching-spec.md)
- [施工计划](p21-issue-explorer-implementation-plan.md)

## 2. 设计目标

- P21 业务逻辑独立于 SAS Viewer 主程序。
- side-car 按需启动，不影响基础 Viewer 的启动速度和稳定性。
- side-car 不直接依赖 Viewer 的内部数据库结构或 UI 类。
- 数据只在本机传递，不启动 TCP 服务，也不访问外部网络。
- 基础版与 P21 扩展版使用同一代码仓库，但分别打包。
- 保留未来独立测试、独立升级和增加 Excel 导出的空间。

## 3. 总体结构

```text
SAS Viewer 主进程
├── Tools 菜单与 side-car 启动器
├── P21 Host Adapter
│   ├── 本地 IPC Server
│   ├── Dataset Snapshot Builder
│   └── P21 Result View Adapter
└── Viewer 原有数据与查询能力
          │
          │ 本地 IPC：QLocalServer / QLocalSocket
          ▼
P21 Issue Explorer side-car 进程
├── P21 Excel Reader
├── Matching Engine
├── SAS WHERE Builder
├── Issue Explorer UI
└── Snapshot Reader
```

SAS Viewer 只提供通用能力：启动进程、提供当前工作区信息、生成只读数据快照、打开结果视图。P21 文件解析、匹配、颜色状态和规则汇总全部留在 side-car 中。

## 4. 推荐目录结构

```text
sas7bdat-viewer/
├── clinical_data_viewer/
│   └── integrations/
│       └── p21/
│           ├── host.py
│           ├── protocol.py
│           ├── snapshot.py
│           └── result_view_adapter.py
├── sidecars/
│   └── p21-issue-explorer/
│       ├── pyproject.toml
│       ├── P21IssueExplorer.spec
│       ├── src/p21_issue_explorer/
│       │   ├── app.py
│       │   ├── models/
│       │   ├── report/
│       │   ├── matching/
│       │   ├── integration/
│       │   └── ui/
│       └── tests/
├── packaging/
│   └── p21-sidecar-manifest.json
└── tests/
    └── integration/p21/
```

主程序中的 P21 integration 层必须保持很薄，不放入 P21 解析或匹配规则。

## 5. 进程和通信

### 5.1 进程模型

- SAS Viewer 使用 `QProcess` 启动和监控 side-car。
- SAS Viewer 创建 `QLocalServer`。
- side-car 使用 `QLocalSocket` 连接 Viewer。
- `QProcess` 只负责启动、退出和异常检测，不承担业务数据通信。

不采用 stdin/stdout JSON Lines。PyInstaller 的 Windows `windowed/noconsole` 应用中，`sys.stdin`、`sys.stdout` 和 `sys.stderr` 可能为 `None`，不适合作为 GUI side-car 的可靠双向通道。Qt 的本地 socket 在 Windows 上可使用命名管道实现本机 IPC。

参考：

- [PyInstaller common issues: standard I/O streams in windowed mode](https://github.com/pyinstaller/pyinstaller/blob/v6.22.3/doc/common-issues-and-pitfalls.rst)
- [Qt for Python QLocalServer](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html)
- [Qt for Python QLocalSocket](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalSocket.html)

### 5.2 会话建立

1. Viewer 生成不可预测的 server name 和 256-bit session token。
2. Viewer 启动本地 server。
3. Viewer 启动 side-car，并通过启动参数传递 server name、token 和 protocol version。
4. side-car 连接后发送 `hello`。
5. Viewer 校验 token 和协议版本后返回 `hello_ack`。
6. 校验失败时立即断开，不处理后续请求。

server name 每次启动重新生成，退出后清理。不得使用固定名称，以免不同 Viewer 实例相互连接。

### 5.3 消息格式

使用 length-prefixed UTF-8 JSON：

```text
4-byte unsigned big-endian payload length
N-byte UTF-8 JSON payload
```

所有消息包含：

```json
{
  "protocol_version": 1,
  "request_id": "uuid",
  "message_type": "request_dataset_snapshot",
  "session_token": "redacted",
  "payload": {}
}
```

日志不得记录 session token、完整患者值或完整 WHERE 条件。

### 5.4 第一版消息

side-car → Viewer：

- `hello`
- `request_workspace_context`
- `request_dataset_snapshot`
- `open_result_view`
- `update_result_view`
- `release_snapshot`
- `shutdown_ack`
- `error`

Viewer → side-car：

- `hello_ack`
- `workspace_context`
- `snapshot_ready`
- `result_view_opened`
- `shutdown`
- `error`

协议中的字段要有版本号；新增可选字段不得破坏旧版本，破坏性变更必须提升 `protocol_version`。

## 6. Dataset Snapshot v1

### 6.1 为什么需要快照

side-car 不应直接读取 Viewer 当前内部缓存数据库。内部数据库属于实现细节，直接耦合会使两边无法独立演进。因此 Viewer 输出一个版本化、只读、中立的数据合同。

### 6.2 文件布局

```text
<session-temp>/p21-snapshots/<snapshot-id>/
├── manifest.json
└── records.sqlite
```

`manifest.json` 至少包含：

- `snapshot_schema_version`
- `snapshot_id`
- `created_at`
- `domain`
- `source_fingerprint`
- `source_row_count`
- `table_name`
- `_source_row` 字段名
- 变量名、SAS 类型、SQLite 类型和可空性
- 包含的变量集合

`records.sqlite`：

- 使用 `records` 表。
- `_source_row` 是当前 Viewer 数据中的稳定候选标识，并建立唯一索引。
- 只包含本次匹配需要的本 domain 变量，而不是无条件复制所有列。
- side-car 以 read-only 模式打开。

### 6.3 变量范围

导入 P21 report 并选择 domain 后，side-car 从 `Variables` 中提取本 domain 所需变量，再向 Viewer 请求快照。Viewer 额外包含可用的稳定定位字段，例如 `STUDYID`、`USUBJID`，但不自动加入不存在的列。

切换 domain 或源数据发生变化时，旧快照失效并重新生成。选择同一 domain 下不同 Rule ID 时，只要变量并集已覆盖且源指纹未变化，可以复用快照。

### 6.4 生命周期

- Viewer 创建并拥有快照。
- side-car 收到 `snapshot_ready` 后读取。
- side-car 不再使用时发送 `release_snapshot`。
- Viewer 退出或 side-car 异常退出时清理本会话快照。
- 启动时可清理超过安全时限的遗留快照。

## 7. P21 Result View

### 7.1 单条 issue

点击 issue 时，Viewer 新开或复用一个 `P21 Result View`：

- 原数据 tab 不变。
- Where 输入框写入可见且可编辑的 Applied WHERE。
- 自动执行筛选。
- strict exact candidate 使用淡绿色；找到后不再搜索 review candidates。
- partial 或 lenient review candidate 使用淡黄色，并展示所有未匹配或宽松匹配变量。
- 无候选时仍可打开空结果视图，保留 Expected WHERE 供排查。

### 7.2 Rule ID 汇总结果

`Filter All Matched Records` 使用候选 `_source_row` 集合生成结果，并在显示前去重。不要把所有 issue WHERE 拼成超长 OR 语句。

汇总结果仍显示来源 domain、Rule ID、P21 issue 数和 strict exact/review candidate 数。Where 区域显示这是 materialized candidate selection；具体可执行 WHERE 仍在单条 issue 视图中检查。

### 7.3 最小 Viewer 改动

现有“新建筛选结果 tab”的通用能力应复用。需要补充的 host adapter 只负责：

- 接收 WHERE 或 `_source_row` 集合。
- 创建/复用结果 tab。
- 应用筛选。
- 根据 candidate metadata 设置行背景和 issue 来源信息。

`MainWindow` 只注册菜单 action 和调用 adapter，不包含 P21 规则。

## 8. 后台任务、取消和缓存

以下操作必须在后台执行，避免阻塞 UI：

- P21 Excel 读取和表头发现。
- Dataset Snapshot 生成。
- Rule ID 匹配。
- 大批候选结果加载。

界面显示阶段和进度；用户可以取消未完成操作。切换 domain、Rule ID 或重新导入 report 时，旧任务结果必须通过 generation ID 丢弃，不能覆盖当前页面。

匹配缓存至少由以下键决定：

```text
report fingerprint + domain + rule id + snapshot source fingerprint + matcher version
```

## 9. 错误隔离

- side-car 启动失败：Viewer 显示可操作错误，核心 Viewer 继续可用。
- IPC 中断：结果视图保留，Explorer 显示连接已断开。
- report 格式错误：只在 side-car 中报告。
- snapshot 生成失败：不把结果解释为 `Not Found`，应显示 `Cannot Evaluate`。
- side-car 崩溃：Viewer 回收快照和本地 server，可允许用户重新打开。

## 10. 打包设计

同一 Git 仓库生成两个制品：

1. 基础版：只包含 SAS Viewer，隐藏 P21 菜单。
2. P21 扩展版：包含 SAS Viewer、side-car executable 和 manifest，显示菜单。

推荐名称：

```text
SASDataViewer-Windows-x64.zip
SASDataViewer-P21-Windows-x64.zip
```

构建脚本增加显式 `-IncludeP21` 选项。是否显示菜单由打包时生成的 side-car manifest 和 executable 完整性共同决定，不仅检查任意同名文件。

side-car 使用独立 `pyproject.toml` 和 PyInstaller spec。第一版允许 Viewer 与 side-car 各自携带 Qt runtime，以优先保证隔离和可重复打包；后续再评估减小体积。

## 11. 数据安全

- 不上传 P21 report、SAS 数据或候选值。
- 临时快照只在本机会话目录中存在。
- side-car 对快照只读。
- 日志默认记录计数、状态、变量名和技术错误，不记录完整患者值。
- 崩溃报告如未来启用，必须先脱敏。

## 12. 后续扩展

Excel issue 导出不在第一版实现。未来由 side-car 生成导出内容，可以调用 Viewer 已有的通用 Excel 能力，但应通过稳定 adapter 使用，不直接引用 Viewer UI 内部类。
