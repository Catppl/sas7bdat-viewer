# 根据 Table Shell 新增 Builder、SAS Code Generator 与 Gallery 的执行手册

## 1. 目的

本手册用于在 SASDataViewer 中，根据一张已经确认的临床 table shell，增量增加一个独立分析模块，并完成以下完整链路：

```text
Table Shell
    ↓
Builder UI
    ↓
Configuration JSON
    ↓
Python Reference Engine
    ↓
Temporary SQLite Result + Long Result / Drilldown（如需要）
    ↓
SAS Code Generator（Jinja2）
    ↓
Table Shell Gallery
```

本手册的目标是让后续开发任务可以重复执行，不需要重新设计项目架构，也不把新业务逻辑继续堆入 `MainWindow`。

## 2. 不可违反的架构原则

### 2.1 不改已有模块

新增 shell 对应新的分析模块。除非需求明确要求共享能力修复，否则不要修改现有 PROC MEANS、Categorical、Rule-based、AE Table、Change from Baseline、Listing 或 Merge 的业务语义。

### 2.2 `MainWindow` 只负责窗口和路由

`MainWindow` 可以增加：

- Tools 菜单入口；
- `show_<module>_builder()`；
- Analysis Dock 的显示、默认宽度和布局恢复；
- 通用结果 Tab、后台 worker 和错误窗口宿主能力。

`MainWindow` 不得包含：

- 分母或统计计算；
- Filter 编译；
- JSON 构造；
- SAS 程序拼接；
- 模块运行状态；
- result context 或 drilldown 业务条件。

### 2.3 每个模块拥有独立 Controller

模块工作流必须放在：

```text
clinical_data_viewer/controllers/analysis/<module>.py
```

顶层 `AnalysisController` 只负责：

- 创建模块 controller；
- 连接 Builder signals；
- 暴露兼容入口；
- 跨模块通用生命周期转发；
- Gallery 路由。

### 2.4 JSON 是 Python 与 SAS 的共同业务 contract

正确流程是：

```text
Builder Selection
    ↓
Domain Config
    ↓
Python Engine
    ↓
configuration JSON-compatible dict
    ↓
SAS Generator
```

SAS Generator 只能翻译 JSON 已经明确的业务语义，不能重新猜：

- count 方法；
- denominator；
- missing policy；
- Total；
- treatment/group order；
- rounding；
- display precision；
- hierarchy 或 sort contract。

### 2.5 SAS 必须由 Jinja2 模板渲染

Python Generator 只负责：

- 验证 JSON；
- 准备 template context；
- 渲染 Filter AST；
- 生成 SAS-safe name、literal 和短 token；
- 调用模板。

SAS 程序结构必须放在：

```text
clinical_data_viewer/codegen/sas/templates/<module>.sas.j2
```

禁止在 Generator 中大量使用 `lines.append()` 手工拼接 SAS。

### 2.6 先有 Python reference，再有 SAS Generator

如 Python reference engine 尚未稳定，先完成 Python、JSON、result 和 tests，再单独增加 SAS Generator。SAS 不应成为第一份业务实现。

## 3. 开工前必须从 Shell 确定的业务 contract

开始写代码前，必须把以下问题写入设计文档并确认。任何会改变结果结构或统计意义的问题不清楚时，必须停下来询问用户。

| 项目 | 必须确定的内容 |
| --- | --- |
| Source | 使用一个 source、独立 population，还是需要 merge |
| Analysis universe | Dataset Filter 在哪一步应用 |
| Row definition | 行是用户配置还是 runtime 从数据发现 |
| Column definition | Treatment、Visit、组合 group 或固定列 |
| Count | records、nonmissing、distinct `USUBJID` 或其他 |
| 大 N / 分母 | Population、same-universe、nonmissing 或其他 |
| Total | 重新计算还是相加；默认必须优先重新计算 |
| Missing | 排除、报错、显示为 `Uncoded` / `(Missing)` |
| Sorting | 用户顺序、raw value、alphabetical、frequency 或自定义 |
| Decimals | base decimals、statistic offsets、最大位数、rounding |
| Display | wide/long、合并统计行、缩进、label、零分母显示 |
| Drilldown | 必须明确“需要”或“不适用”；需要时确认 records、subjects、denominator subjects、Total 和 missing 语义 |
| Codegen | SAS source 限制、Merge 是否拒绝、是否 runtime 重新排序 |

### 3.1 Shell 只是布局样例，不是计算 contract

Shell 中的 `Drug A`、`Week 1`、`PARAMCD` 等示例值不得直接写死到 Engine。实际变量、level 和顺序必须来自用户配置或 JSON 明确规定的 runtime contract。

### 3.2 区分 reusable rule 与 runtime snapshot

数据驱动模块应保存两类信息：

- reusable rule：例如 `total_frequency desc + alphabetical`；
- runtime snapshot：例如本次 Python run 的 resolved hierarchy。

未来 SAS 程序必须使用 reusable rule 在新数据上重新计算，不得把 snapshot 当成永久排序输入。

## 4. Shell 资源制作标准

### 4.1 文件位置

```text
docs/<module>-shell.docx
clinical_data_viewer/assets/shells/<module>.docx
clinical_data_viewer/assets/shells/<module>.png
```

`docs/<module>-shell.docx` 是唯一可手工编辑的 shell 来源。`assets/shells`
中的 DOCX 是随应用打包的同步副本，PNG 是从该 DOCX 渲染后供 Gallery 和
Builder Preview 使用的资源。更新源 DOCX 后，必须同时覆盖 packaged DOCX 和 PNG；
不得分别手工维护三份内容。

`docs` 中可以使用便于阅读的连字符文件名，packaged resource 使用 Python module key
的下划线命名。例如：

```text
docs/proc-means-vertical-shell.docx
  -> clinical_data_viewer/assets/shells/proc_means_vertical.docx
  -> clinical_data_viewer/assets/shells/proc_means_vertical.png
```

### 4.2 DOCX 标准

- 黑白临床三线表；
- 顶线、表头下横线、底线完整；
- 不使用整表网格；
- Item、统计行、缩进和表头层级清楚；
- 示例数据使用 `x`、`xx.x` 等 shell 占位符；
- 不把真实项目、受试者或路径写进 shell；
- 是否有标题、表号、footnote 由用户确认。

### 4.3 PNG 生成和验收

必须从最终 DOCX 渲染 PNG，不要手工制作一张与 DOCX 不一致的图片。

验收：

- 每页均已渲染和目视检查；
- 无裁切、重叠、断线或缺字；
- PNG 能在应用中通过 `resource_path()` 加载；
- 打包配置包含 `clinical_data_viewer.assets.shells = ["*.png", "*.docx"]`。

### 4.4 Preview 规则

- 应用内 Gallery 和 Builder 的 Shell Preview 只展示 PNG，不直接预览 DOCX；
- DOCX 更新后必须重新渲染并覆盖对应 PNG，避免两个版本漂移；
- 如果 Builder 也需要 Preview，复用现有 `ShellPreviewDialog` 和 `shell_preview_requested` signal；
- 测试应验证 DOCX/PNG 均存在、PNG 文件头合法且 packaged resource 可读取。

## 5. 推荐文件结构

假设模块 key 为 `<module>`：

```text
clinical_data_viewer/
├── <module>/
│   ├── __init__.py
│   ├── models.py
│   ├── engine.py
│   ├── result_store.py
│   ├── configuration.py
│   └── drilldown.py              # 仅需要时
├── ui/
│   └── <module>_builder.py
├── controllers/analysis/
│   └── <module>.py
├── codegen/sas/
│   ├── <module>_generator.py
│   └── templates/
│       └── <module>.sas.j2
└── assets/shells/
    ├── <module>.docx             # docs 中维护源的打包副本
    └── <module>.png              # Gallery / Builder Preview

docs/
├── <module>-design.md
└── <module>-shell.docx

tests/
├── test_<module>.py
├── test_<module>_configuration.py
├── test_<module>_sas_codegen.py
├── test_<module>_shell.py
└── test_<module>_drilldown.py     # 仅需要时
```

不要把所有逻辑写入一个文件。

### 5.1 各类文件的固定存放位置

以下位置是新 Table Builder 的默认约定。除非现有模块已有经过测试的不同约定，
不要把同一类文件散落到其他目录。

| 文件或产物 | 固定位置 | 说明与生命周期 |
| --- | --- | --- |
| 可编辑 Shell DOCX | `docs/<module>-shell.docx` | Shell 的唯一维护源；修改后必须重新渲染 PNG。 |
| 打包 Shell DOCX | `clinical_data_viewer/assets/shells/<module>.docx` | 维护源的逐字节同步副本；由 package data 和 PyInstaller 收入应用。 |
| Shell PNG | `clinical_data_viewer/assets/shells/<module>.png` | Gallery 和 Builder Preview 实际加载的图片；必须由最终 DOCX 渲染。 |
| 需求、设计与审查记录 | `docs/<module>-design.md` 或明确命名的 `docs/*.md` | 记录已确认 contract、边界、延期项和实施计划，不放入业务模块目录。 |
| Domain models / validation | `clinical_data_viewer/<module>/models.py` | 保存 editable config、枚举和业务 validation；不放 UI 状态。 |
| Python reference engine | `clinical_data_viewer/<module>/engine.py` | 实现已冻结的计算语义；不含 Qt UI 和 SAS 文本拼接。 |
| Result store / long result | `clinical_data_viewer/<module>/result_store.py` | 创建临时 SQLite 结果、metadata 和 cell map；不写回 source dataset。 |
| Drill-down | `clinical_data_viewer/<module>/drilldown.py` | 仅在模块需要 drill-down 时创建；复用同一计算条件。 |
| Configuration JSON builder | `clinical_data_viewer/<module>/configuration.py` | 从 config、DatasetHandle 和 runtime resolved 信息构建稳定 JSON contract。 |
| Builder UI | `clinical_data_viewer/ui/<module>_builder.py` | 只负责输入、显示和 signals；不放业务计算。 |
| 模块 Controller | `clinical_data_viewer/controllers/analysis/<module>.py` | 负责 source 绑定、worker、result lifecycle、code dialog 和模块专属路由。 |
| Analysis 总入口 | `clinical_data_viewer/controllers/analysis_controller.py` | 只注册/协调独立 Controller；不要重新塞入模块业务流程。 |
| SAS Generator | `clinical_data_viewer/codegen/sas/<module>_generator.py` | 验证 JSON、准备模板 context、调用 Jinja2；不维护大段 SAS 字符串。 |
| SAS Jinja2 模板 | `clinical_data_viewer/codegen/sas/templates/<module>.sas.j2` | 保存可读 SAS 程序结构；通用片段仅在语义一致时复用。 |
| R Generator（如有） | `clinical_data_viewer/codegen/r/` 与 `codegen/r/templates/` | 只有需求明确要求时新增；仍消费相同业务 JSON。 |
| 模块单元测试 | `tests/test_<module>.py` | 覆盖 config validation 与 Python reference semantics。 |
| JSON / codegen / UI 测试 | `tests/test_<module>_configuration.py`、`test_<module>_sas_codegen.py`、`test_<module>_builder*.py` | 按职责拆分，不把所有断言堆入一个测试文件。 |
| Shell 同步测试 | `tests/test_<module>_shell.py` | 验证源 DOCX、packaged DOCX、PNG 及资源读取；建议比较两份 DOCX bytes。 |
| Gallery 注册 | `clinical_data_viewer/ui/table_shell_gallery.py` | 只保存卡片 metadata 与 preview 路径；打开 Builder 仍经 MainWindow 路由。 |
| 用户文档 | 根目录 `README.md` 与 `README.en.md` | 同步说明入口、能力、限制和项目结构。 |
| 验收截图 | `docs/screenshots/` | 只保存有长期文档价值的截图；测试临时截图不要提交。 |
| Windows 打包配置 | `SASDataViewer.spec` | 确认整个 `assets/shells` 被收集；不要逐个硬编码 shell 文件。 |
| Python package data | `pyproject.toml` | 保持 `clinical_data_viewer.assets.shells = ["*.png", "*.docx"]`。 |

### 5.2 运行时生成文件的位置

运行时产物不进入上述源码目录：

```text
TempManager 管理的当前会话目录/
└── <result-id>/
    ├── dataset.sqlite
    └── <module>_config.json
```

- `dataset.sqlite` 保存 wide result、long result、cell map 等模块结果；具体表结构由
  `result_store.py` 负责；
- `<module>_config.json` 与该 result 同生命周期，关闭结果并释放临时目录后一起删除；
- source dataset 的临时只读副本和分析 result 目录都由 `TempManager` 管理，不在模块中
  硬编码 Windows、macOS 或 Linux 临时路径；
- 不把 JSON、SQLite、生成的 SAS/R 程序写入 source dataset 所在目录；
- SAS/R Code Dialog 默认只预览内存中的代码。用户点击 `Save As…` 后，程序保存到用户
  自行选择的位置；该保存文件不是项目资源，也不参与 result lifecycle；
- 测试创建的 SQLite、JSON、DOCX 渲染中间文件和截图必须使用测试临时目录，测试结束后
  清理，不要提交到仓库。

## 6. Domain Config 与 Validation

### 6.1 Editable Config

`models.py` 中定义不可变 config：

```python
@dataclass(frozen=True, slots=True)
class ModuleConfig:
    analysis_variables: tuple[str, ...]
    group_variables: tuple[str, ...]
    compiled_filter: CompiledFilter
    filter_text: str
    include_total: bool
    percent_digits: int
```

只保存用户可编辑业务配置，不要把 runtime resolved levels 强行塞进 Builder model。

### 6.2 Validation 必须在运行前完成

至少验证：

- source 已打开且 cache complete；
- referenced variables 存在；
- numeric/character type 符合要求；
- variable selection 无非法重复；
- Filter 使用现有 `FilterEngine` 编译；
- denominator/population 所需变量存在；
- treatment/subject type compatibility；
- decimal、sort、missing policy 在支持范围内；
- source kind 与 codegen 能力匹配。

失败必须给用户明确错误，不允许 silent fallback。

## 7. Configuration JSON

### 7.1 建议顶层结构

按模块业务调整，但至少清楚区分：

```json
{
  "type": "<module_type>",
  "version": 1,
  "input": {},
  "variables": {},
  "dataset_filter": {},
  "calculation": {},
  "display": {},
  "targets": {}
}
```

涉及 population、rows、hierarchy、sort、total 时，应分别建立独立 block，不要把所有字段平铺到顶层。

### 7.2 Filter contract

所有可生成跨语言代码的 Filter 必须保存：

```json
{
  "language": "sas_like",
  "text": "SAFFL = \"Y\"",
  "ast": {}
}
```

AST 必须复用现有 serializer。不得保存 SQLite SQL 作为 SAS Generator 的业务输入，也不得复制新的 WHERE parser。

### 7.3 生命周期

成功运行后，在 result temporary directory 写入：

```text
dataset.sqlite
<module>_config.json
```

关闭 result 后由现有 temporary lifecycle 一起清理。不要写入 source dataset 文件夹。

## 8. Python Engine

### 8.1 数据原则

- SQLite 继续保存 raw value；
- WHERE、SORT、MERGE、COMPARE、analysis 使用 raw value；
- 仅 display 层应用 format；
- 大数据优先使用 SQLite `GROUP BY`、`COUNT(DISTINCT ...)`；
- 避免逐行 Python loop 和整表一次性 pandas DataFrame；
- denominator 整张表只计算一次；
- treatment/group levels 尽量一次 resolve。

### 8.2 结果语义

- 小 `n`、大 `N`、records、subjects 必须明确区分；
- SOC、Total 等聚合通常必须独立 distinct count，不能由子项相加；
- denominator filter 不得错误继承 numerator row filter；
- Total drilldown 不得增加 treatment 条件；
- missing subject 默认排除时，应统一处理 `NULL`、空字符及项目既有 missing helper 所定义的空白。

### 8.3 Result Store

复用 session temporary SQLite 和普通 Viewer：

- paging；
- WHERE；
- sorting；
- visible columns；
- copy；
- CSV export。

为结果定义唯一 `kind`。如有 long result，使用独立 kind，并保存明确的 `row_order`、`trt_order` 或其他排序字段，不依赖 SQLite 插入顺序。

### 8.4 Drill-down 必查清单

每个新 Builder 在设计阶段都必须明确记录 Drill-down 是“需要”还是“不适用”，不得因为
Shell 没有画出交互而默认省略。只要结果单元格代表一组可追溯的 source records，原则上
就应提供 Drill-down；若不提供，必须在设计文档和 README 限制中写明原因。

需要 Drill-down 时，必须检查完整链路，而不是只增加一个 `drilldown.py`：

- `result_store.py` 保存 result cell 到 source universe 的明确映射；
- cell map 保存 raw typed values，不从显示 label、格式化数字或拼接表头反向解析；
- `DatasetMetadata` 明确哪些结果列允许 Drill-down；
- `DatasetTab` 对对应 result `kind` 发出专属 signal；
- 双击结果单元格和右键 `Drill Down to Source Rows` 使用同一条 signal 路径；
- `MainWindow` 只连接 signal 到模块 Controller，不构造筛选条件；
- 模块 Controller 使用当前 result 的 context、config 和 source handle；
- Query Tab 显示可复制的 SAS-like WHERE，同时使用 compiled filter 查询 raw values；
- result 经过 WHERE、排序或分页后，必须通过真实 `_source_row` / cell map 定位，不能把
  当前 view row 当成永久结果行号；
- Total 单元格重新使用 Total universe，不错误附加 treatment/group 条件；
- numerator、denominator、subjects、missing statistic 分别复用各自真实计算条件；
- source/result 已关闭、context 丢失、单元格无值或 mapping 缺失时给出明确提示；
- Query result 仍走 session temporary lifecycle，并在 stale async callback 时安全清理。

如果 Drill-down 明确不适用，也应增加测试，确认双击不会发出错误请求、右键菜单保持
disabled，且文档没有宣称该功能可用。

## 9. Builder UI

### 9.1 Builder Selection

UI 文件应定义只读 selection dataclass，作为 signal payload：

```python
@dataclass(frozen=True, slots=True)
class ModuleBuilderSelection:
    filter_text: str
    analysis_variables: tuple[str, ...]
    include_total: bool
```

Source Dataset 通过 `source_selected` signal 交给模块 controller 绑定，不需要把 `DatasetTab` 塞入 selection 或业务 JSON。

### 9.2 Source 行为

- Builder 打开后允许用户从已经打开的数据集中选择 source；
- 页面提示未打开数据集时先打开数据集；
- Builder 内 filter 独立于 Viewer 后续 WHERE 修改；
- source 关闭时给出警告；
- 除非用户点击 `Clear`，不得清空已输入配置；
- result Tab 关闭不得自动清空 Builder。

### 9.3 UI 规范

- 复用主界面的浅蓝白样式；
- 主内容放在可横向、纵向滚动区域；
- 窄 Dock 下 Remove、Up、Down 和底部按钮仍可见；
- ComboBox/SpinBox 禁止鼠标滚轮误改值，使用 click-only widget；
- Run 使用后台 worker，不阻塞 UI；
- busy 时禁用 Run 和不适用的 Code Generator；
- Merge source 如不支持 SAS codegen，按钮必须保持 disabled，busy 恢复后不能错误 enable。

### 9.4 AnalysisPanel 接入

在 `AnalysisPanel` 中：

1. 创建 Builder 实例；
2. 增加 `show_<module>_tab()`；
3. 通过 `_activate()` 动态加入 tab；
4. 不要在应用启动时默认打开全部 Builder tabs。

## 10. 模块 Controller

`controllers/analysis/<module>.py` 应拥有：

- Engine；
- SAS Generator；
- source/population 绑定；
- input tabs；
- result contexts；
- run / completed / failed；
- code generation；
- long result；
- drilldown；
- retained temporary directory 生命周期。

典型入口：

```python
def show_builder(self) -> None:
    self.refresh_sources()
    self._panel.show_<module>_tab()
```

运行必须通过 host：

```python
self._host.submit_analysis_task(
    builder,
    lambda worker: self._engine.run(..., worker.report),
    completed,
    failed,
)
```

## 11. AnalysisController 与 MainWindow 接线

### 11.1 AnalysisController

在 `AnalysisController.__init__()` 中创建模块 controller 并连接 signals：

```python
self.<module> = ModuleController(self, temp_manager, settings)

builder.run_requested.connect(self.<module>.run)
builder.sas_code_requested.connect(self.<module>.generate_sas_code)
builder.source_selected.connect(self.<module>.select_source)
builder.cleared.connect(self.<module>.clear_source)
```

顶层 facade 可保留短转发方法以兼容 MainWindow 和既有调用者，但不保存模块业务逻辑。

### 11.2 MainWindow

新增公开入口，并由这里统一负责 Dock：

```python
def show_<module>_builder(self) -> None:
    self.analysis_controller.show_<module>_builder()
    self.analysis_dock.setMinimumWidth(DEFAULT_WIDTH)
    self.analysis_dock.show()
```

如果模块需要紧凑宽度，可以在这里设置 `resize()`。不要把宽度设置复制到 Gallery 或模块 controller。

### 11.3 Export、Menu 与 View Action 清单

根据模块能力同步检查：

- `clinical_data_viewer/<module>/__init__.py` 导出 Config、Engine 和 result helper；
- `clinical_data_viewer/controllers/analysis/__init__.py` 导出模块 Controller；
- `clinical_data_viewer/codegen/__init__.py` 导出 configuration builder；
- `clinical_data_viewer/codegen/sas/__init__.py` 导出 SAS Generator；
- `MainWindow._create_actions()` 创建 Tools / View actions；
- `MainWindow._create_menu()` 把 action 放到正确位置；
- Long Result action 仅在当前 result kind 匹配时 enabled；
- `MainWindow._sync_active_tab()` 不得漏掉新 result kind；
- 关闭 source/result 时，将生命周期通知转发给模块 controller。

不要为了注册模块，把 Engine、JSON 或 codegen 逻辑搬进上述入口文件。

## 12. SAS Code Generator

### 12.1 Generator 输入

Generator 必须只接收 JSON-compatible configuration dict：

```python
class SasModuleGenerator:
    def generate(self, configuration: dict[str, object]) -> str:
        context = self._prepare(configuration)
        return self.environment.get_template("<module>.sas.j2").render(**context)
```

不得直接接收 Engine、SQLite SQL、Builder widget 或 DatasetTab。

### 12.2 Validation

Generator 至少验证：

- `type`；
- `version`；
- 必需 blocks；
- reference engine；
- count/denominator/total/missing/rounding contract；
- referenced variables 和 metadata；
- source kind 和 format；
- Merge source 是否明确拒绝。

不支持的 contract 必须 `ValueError`，禁止默默生成另一种算法。

### 12.3 Filter AST

复用：

```text
clinical_data_viewer/codegen/sas/filter_renderer.py
```

不要从 SQLite SQL 翻译，也不要把原始 Filter text 作为唯一实现。

### 12.4 SAS 可读性

SAS template 必须：

- 使用简短有意义的 dataset/variable 名；
- 按 Prepare source、Denominator、Statistics、Ordering、Final result、Cleanup 分块；
- SQL 正常换行；
- 注释适量；
- 不依赖项目私有宏库；
- 不生成复杂 macro framework；
- Item label、格式规则等人工常改内容集中在一处；
- 最终 dataset 不得在 cleanup 中被删除。

示例命名：

```text
src
pop
denom
stat_aval
subj_aval
long
final
row_ord
trt_ord
base_dec
disp_dec
```

### 12.5 Runtime 与固定配置

- 应随新数据变化的 treatment levels、frequency sort、precision 等必须在 SAS runtime 计算；
- 用户在 JSON 中明确固定的 row order、label、sort contract 必须遵守；
- 不得使用 `resolved_hierarchy` 固定数据驱动表的未来排序；
- numeric variables 用 raw numeric semantics，不用 `vvalue()` 把 formatted text 当 grouping key。

## 13. Gallery 接入

### 13.1 注册卡片

在 `ui/table_shell_gallery.py` 的 `TABLE_SHELLS` 中增加：

```python
TableShell(
    "<module>",
    "Human-readable Title",
    "Short description",
    "Summary",  # 或 Efficacy / Safety
    "clinical_data_viewer/assets/shells/<module>.png",
)
```

如果暂时没有 PNG，保留 `preview_path=None`，Gallery 会使用统一占位图。

### 13.2 Gallery 路由必须经过 MainWindow

在 `AnalysisController._open_table_shell_builder()` 增加：

```python
host_routes = {
    "<module>": "show_<module>_builder",
}
```

不要直接把 Gallery 的主要路由写成：

```python
self.<module>.show_builder
```

因为直接调用会绕过 MainWindow 的 Analysis Dock 显示和宽度设置。模块 controller 入口只作为非 MainWindow 测试宿主的 fallback。

### 13.3 Gallery 验收场景

必须测试：

```text
隐藏 Analysis Dock
    ↓
打开 Table Shell Gallery
    ↓
点击目标卡片
    ↓
点击 Open Builder
    ↓
Analysis Dock 显示
    ↓
目标 Builder 成为当前 Analysis tab
```

## 14. 测试矩阵

### 14.1 Domain / Engine

- config validation；
- Filter；
- missing；
- count 和 denominator；
- Total 重新计算；
- sorting；
- decimal/rounding；
- duplicate records；
- numeric/character group；
- source/population 独立；
- Merge source（如果 Python 支持）。

### 14.2 Result / Lifecycle

- wide result；
- long result；
- explicit row/trt order；
- result kind；
- JSON 与 result 同生命周期；
- close result 释放 retained paths；
- close source warning；
- Builder 配置只由 Clear 清除。
- cell map 使用 raw typed group/item/treatment values；
- 普通 cell、missing cell 与 Total cell 的 Drill-down filter；
- filtered/sorted result view 仍定位到正确 source cell mapping；
- stale context / closed source / async result cleanup。

### 14.3 UI

- 所有按钮显示和点击；
- 横向/纵向滚动；
- 窄 Dock 下控件可见；
- Up/Down 顺序；
- click-only selectors；
- busy state；
- Merge source codegen disabled；
- Gallery preview；
- Gallery 打开 Builder 后 Dock 真正显示。
- Drill-down 双击和右键入口均可用，且非统计列保持 disabled；
- Query Tab 打开后 WHERE 框展示实际追溯条件。

### 14.4 SAS Generator

- JSON validation；
- Filter AST；
- source/population 独立；
- count/denominator/Total；
- runtime ordering；
- decimals；
- name literals/string literals；
- human-readable section comments；
- final output schema；
- cleanup 不删除 final dataset；
- Merge rejection；
- Jinja template 被加载；
- 生成代码不使用不应参与逻辑的 runtime snapshot。

### 14.5 建议命令

先运行模块定向测试：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q \
  tests/test_<module>.py \
  tests/test_<module>_configuration.py \
  tests/test_<module>_sas_codegen.py \
  tests/test_<module>_shell.py
```

再运行控制器和 Gallery 回归：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q \
  tests/test_analysis_controller.py \
  tests/test_table_shell_gallery.py \
  tests/test_ui_smoke.py
```

最后运行全量测试。若全量存在已有长时测试或环境依赖，必须明确区分“定向通过”和“全量未完成”，不得把部分测试写成全量通过。

## 15. 推荐施工顺序

严格按以下顺序，避免 UI 和 Generator 先行造成业务语义漂移：

1. 审核 shell，冻结业务问题，并明确 Drill-down 是需要还是不适用；
2. 创建 DOCX，渲染并审核 PNG；
3. 建立 `models.py` 和 validation；
4. 建立 Python Engine tests；
5. 实现 Engine；
6. 实现 result store / long result；如需 Drill-down，同时实现 cell map、filter 和 Query Tab；
7. 建立 configuration JSON v1 tests；
8. 实现 configuration builder/writer；
9. 实现 Builder UI；
10. 实现模块 Controller；
11. 接入 AnalysisPanel、AnalysisController、MainWindow；
12. 运行 Python/UI 回归；
13. 规划并实现 SAS Generator；
14. 加入 Jinja2 template 和 codegen tests；
15. 注册 Gallery card 和 preview；
16. 增加隐藏 Dock → Gallery → Builder 的真实 UI smoke test；
17. 更新中英文 README；
18. 运行定向测试、相关回归和全量测试；
19. 检查 `git diff --check` 和工作区；
20. 用户明确要求后再 commit / push。

## 16. 完成验收标准

只有以下条件全部满足，模块才算完成：

1. Shell DOCX 和 PNG 一致且经过目视检查；
2. Builder 可选择已打开 source；
3. Builder 配置不会被 tab 切换或 result close 意外清空；
4. Python result 与确认的 shell 语义一致；
5. JSON 明确表达所有 calculation contract；
6. SAS Generator 只消费 JSON；
7. SAS 使用 Jinja2 模板；
8. 生成代码可读且命名符合人类习惯；
9. Result 使用 session temporary SQLite；
10. 已明确记录 Drill-down 是否适用；适用时 cell map、双击、右键、WHERE Query Tab、Total/missing 边界全部可用；
11. Gallery 卡片能显示 preview；
12. Gallery 路由经过 MainWindow；
13. Analysis Dock 隐藏时仍能从 Gallery 正确打开 Builder；
14. 定向、相关回归和全量测试状态均如实记录；
15. 不破坏已有模块。

## 17. 可直接复用的后续任务指令

```text
请严格按照 docs/table-shell-builder-codegen-gallery-execution-playbook.md，
基于已确认的 <MODULE NAME> shell 新增独立模块。

要求：
1. 不修改现有分析模块的业务逻辑。
2. 不在 MainWindow 中加入业务计算。
3. 模块逻辑放入独立 engine/models/configuration/result_store/controller。
4. Python Engine 是 reference engine。
5. JSON 是 Python 与 SAS 的唯一业务 contract。
6. SAS Generator 只接收 JSON-compatible dict，并使用 Jinja2 template。
7. Filter 必须复用现有 FilterEngine、AST serializer 和 SAS renderer。
8. Result 使用 session temporary SQLite，并复用现有 Viewer。
9. Builder 配置只在用户点击 Clear 时清除。
10. Gallery 必须先 preview，再确认打开 Builder。
11. Gallery 路由必须经过 MainWindow 的 show_<module>_builder()，保证 Analysis Dock 显示和宽度设置。
12. 补齐文档中要求的 domain、engine、JSON、UI、codegen、Gallery 和 lifecycle tests。
13. 完成后运行相关 regression tests，并如实报告全量测试状态。
14. 明确检查 Drill-down 是否适用；适用时实现 cell map、双击/右键、Query Tab 和 Total/missing tests，不适用时记录原因并保持入口 disabled。

遇到会改变 Count、Denominator、Missing、Total、Sorting、Decimals、
Drilldown 或最终表结构的不明确需求时，停止实现并询问用户。
```

