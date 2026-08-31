# Filter History 本地系统时间显示 Issue

状态：已实施  
记录日期：2026-08-31

## 1. 用户可见问题

Filter History 的 `Date / Time` 列直接显示历史记录中保存的 ISO 时间。当前保存值使用 UTC，因此在 UTC 以外的电脑上，显示时间可能与系统托盘/系统设置中的本地时间不一致。

期望行为：

- Filter History 展示时间跟随运行程序电脑的系统时区。
- 用户改变系统时区后，重新打开或刷新 Filter History 应按新的本地时区展示。
- 历史记录的真实时间点不应因展示转换而改变。

## 2. 已确认的原因与涉及程序

### 时间写入

文件：`clinical_data_viewer/filter_history.py`

`FilterHistory.add()` 当前使用：

```python
datetime.now(UTC).isoformat(timespec="seconds")
```

因此 `executed_at` 以带 UTC offset 的 ISO 字符串写入 SQLite。这个存储 contract 本身正确，能够稳定表示真实时间点，不建议改成无时区的本地时间。

### UI 展示

文件：`clinical_data_viewer/ui/history_dialog.py`

`HistoryDialog.refresh()` 当前直接把：

```python
entry.executed_at
```

写入 `Date / Time` column 和 tooltip，没有：

1. 解析 ISO timestamp；
2. 转换为系统当前时区；
3. 格式化为用户容易阅读的本地日期和时间。

因此根因在 History Dialog 的 display formatting，而不是 Filter History 的写入时间错误。

## 3. 已实施方案

本次保持 SQLite UTC 数据不变，只修改显示层：

1. 增加可单元测试的 `format_history_timestamp()` helper。
2. 使用 `datetime.fromisoformat()` 解析 `executed_at`。
3. 对带 offset 的时间调用 `astimezone()`，不传目标时区，使 Python 使用电脑当前系统时区。
4. UI column 显示本地时间，例如 `YYYY-MM-DD HH:MM:SS`。
5. tooltip 显示本地时间、timezone abbreviation 和 UTC offset，便于审计。
6. 无法解析的旧值或异常值不得导致窗口打不开；应回退显示原始字符串。
7. 不批量重写现有 SQLite history，不做数据库 migration。

## 4. 兼容性与边界

- 现有带 `+00:00` 的 UTC timestamps：转换为当前系统时区。
- 已带其他 offset 的 timestamps：先按原 offset 还原真实时间点，再转为系统时区。
- 旧版本可能存在的 naive timestamp：实施时必须明确兼容策略；建议将其视为历史 UTC contract，而不是直接猜成本地时间。
- malformed timestamp：原样展示并保持 Filter History 其他条目可用。
- 历史排序继续按现有记录 ID 倒序，不因格式化文本改变排序。
- 不改变 Filter History 的保存、去重、恢复、删除和清空行为。
- 不修改 WHERE text、Filter Engine 或 dataset filter 状态。

## 5. 已增加的测试与验收

已覆盖：

1. UTC timestamp 在 `Asia/Shanghai` 下正确显示为 UTC+8。
2. 带非 UTC offset 的 timestamp 转为指定系统时区后仍表示同一时间点。
3. 夏令时地区使用目标日期对应的正确 offset，而不是固定时差。
4. 系统时区改变后，新建/刷新 History Dialog 使用新的时区。
5. naive legacy timestamp 按最终约定正确处理。
6. malformed timestamp 原样显示，不抛异常。
7. Date / Time column 与 tooltip 的本地时间一致。
8. Current dataset / All datasets、Use Condition、Delete、Clear History 行为不回归。
9. 全量测试通过。

## 6. 实施结果

已修改 `filter_history.py` 和 `history_dialog.py` 的 display path；SQLite schema、UTC storage、Filter History 保存/恢复/删除和 WHERE 行为保持不变。
