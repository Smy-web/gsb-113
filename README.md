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
- 时间戳约定：所有写入（`timestamp`、`transmission_time`、深度记录的 `timestamp`）在落库前
  统一归一化为定宽的 UTC ISO-8601 文本（`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`），
  使字典序等于时间序。调用方可以传 `datetime` 对象或 ISO-8601 文本
  （`T` 或空格分隔、带或不带微秒、带 `Z` / 偏移量 / 不带时区）；
  **不带时区的输入一律按 UTC 处理**。读路径不改写库里的历史文本，返回的 `timestamp` 仍是 str 或 None。
- 回传状态约定：`save_drilling_log` 对**已存在**的 `log_id` 只回写正文与摘要列，
  `transmitted` / `transmission_time` / `error_message` 三列保持原值；
  新建 `log_id` 时按调用方给的值写入。`update_transmission_status(log_id, True)` 不显式给
  `transmission_time` 时自动落当前 UTC 时间。
- 深度序列：`get_ice_core_depth_series(log_id=None, limit=500, offset=0)` 不传 `log_id` 时
  同一日志的点连续排列、组内按 `depth_meters` 升序；`log_id=""` 按字面过滤（查不到记录），
  不会被当成「查全部」。`count_ice_core_depth_records(log_id=None)` 返回符合条件的总条数，
  配合 `offset` 判断是否还有下一页。

## 运行

    ~/venvs/gsb-farm/bin/python -m pytest tests/ -q

解释器与依赖版本见 `requirements-task.txt`。不要 pip install，不要联网。

### 本次做题人填写

**取舍点：回写正文时要不要顺带接受调用方给的回传三列。**

选择：让 `save_drilling_log` 彻底不碰 `transmitted` / `transmission_time` / `error_message`
三列——对已存在的 `log_id` 走只更新正文与摘要列的 `UPDATE`，新建时才按调用方给的值写入。

理由：现场系统的真实故障就是「两步写」的第二步（回写摘要）用 `INSERT OR REPLACE`
把整行覆盖，回传三列被重置成默认值，已回传的日志重新进入待回传队列，铱星链路隔一天
又发了一遍。让 `save_drilling_log` 职责单一（只管正文与摘要）、回传状态只能由
`update_transmission_status` 改写，从结构上消除了这一类回归：任何调用点都不可能因为
漏传或误传某个键而把回传状态改回去。

不选「部分更新、只覆盖显式给出的键」的原因：它要求在每个调用点区分「没给这个键」和
「给了 False」，而调用方传的是普通 dict，`log.get('transmitted', False)` 这类写法遍地都是，
任一调用点漏传就可能静默把已回传状态改回未回传——这正是本次事故的同款隐患。

代价：导入历史台账时，对已回传的记录要在 `save_drilling_log` 之后多调一次
`update_transmission_status` 才能落回传状态；这是两次显式调用，语义清晰，可接受。
