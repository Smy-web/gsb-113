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

## 运行

    ~/venvs/gsb-farm/bin/python -m pytest tests/ -q

解释器与依赖版本见 `requirements-task.txt`。不要 pip install，不要联网。

### 本次做题人填写

（把你自己拿主意的那一处写在这里：选了哪种做法、为什么不选另一种、代价是什么。）
