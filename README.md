# 冰封纪要 · 钻探台账持久层

极地冰芯钻探队现场系统的台账存取层。钻探日志、冰芯深度序列、音频上传记录都落在同一个
SQLite 文件里；铱星链路的回传任务从同一张表里取「待回传」清单，前端的「最近日志」列表
和深度曲线也直接读这一层。

## 目录

- `backend/database.py`：SQLite 存取层。建表、钻探日志读写、冰芯深度序列读写、回传状态更新、待回传队列。
- `backend/models.py`：跨模块传递的数据结构（pydantic 模型）——钻探日志、冰芯数据、转写片段、气候事件、回传状态。
- `tests/`：现有测试，只覆盖正常路径。
- `conftest.py`：把仓库根加进 `sys.path`，测试里统一用 `from backend.database import ...` 导入。

## 数据契约

- 三张表：`drilling_logs`、`audio_uploads`、`ice_core_depth_series`。表名与列名是与前端和回传脚本的既有约定。
- `drilling_logs` 一行是一份钻探日志。`ice_core_data`、`transcript`、`climate_events`、`sample_allocation`
  四列存 JSON 文本；`transmitted` 存 0/1；`timestamp` 与 `transmission_time` 存文本。
- `transmitted`、`transmission_time`、`error_message` 三列记录铱星链路的回传结果。
- 读函数返回普通 dict，键名与列名一致；`transmitted` 是 Python bool。
- 一份钻探日志的正文（现场数据、转写、气候事件）和它的摘要、样品分配方案，
  在现场系统里是分两步写进来的：先落正文，等 AI 摘要跑完再回写同一条 `log_id`。
- `timestamp` 与 `transmission_time` 落库前统一归一化为定宽 UTC 文本
  `YYYY-MM-DDTHH:MM:SS.ffffff`（微秒六位、不带时区后缀），使字典序等于时间序。
  调用方可以传 `datetime` 对象或 ISO-8601 文本（`T` 或空格分隔、带或不带微秒、
  带 `Z` / `+08:00` 等时区偏移）；**不带时区的输入一律按 UTC 处理**。
  读路径不改写库里的历史文本，返回的 `timestamp` 仍是 str 或 None。
- `save_drilling_log()` 对已存在的 `log_id` 只更新正文与摘要字段，
  `transmitted` / `transmission_time` / `error_message` 三列保持原值；
  回传状态只能由 `update_transmission_status()` 改变。

## 运行

    ~/venvs/gsb-farm/bin/python -m pytest tests/ -q

解释器与依赖版本见 `requirements-task.txt`。不要 pip install，不要联网。

### 本次做题人填写

选择了第一种：`save_drilling_log` 彻底不碰回传三列——对已存在的 `log_id`，
先读出该行的 `transmitted` / `transmission_time` / `error_message` 原值，
`INSERT OR REPLACE` 时原样写回；新建 `log_id` 时才照调用方给的值落库。

理由：现场系统的写路径是「先落正文、等摘要跑完再回写同一条 log_id」，回写时
调用方手里根本没有回传状态，职责单一的接口让「补摘要」这个动作在语义上不可能
弄丢回传结果，也不需要每个调用点都小心地区分「没传这个键」和「传了 False」。
部分更新方案把正确性寄托在所有调用点都不漏传上，漏一处就会把已回传的记录
静默改回未回传，正是这次铱星重复回传事故的根因，不能再留这个口子。

代价：导入历史台账时，对已回传的记录要在 `save_drilling_log` 之外再调一次
`update_transmission_status` 才能补上回传状态，是两步操作而不是一步到位。
