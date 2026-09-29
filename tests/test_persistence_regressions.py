"""回归用例：钉住本次修复的每条需求。

负例说明：test_resave_preserves_transmission_state 针对的是
save_drilling_log 里「先读出这条日志已有的回传状态、再决定这次写什么」
那段判断——把它删掉或注释掉，这条用例应变红。
"""
import glob
import os
import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend import database
from backend.models import (
    ClimateEvent,
    DrillingLog,
    IceCoreData,
    TranscriptSegment,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ice_core.db"))
    database.init_database()
    return database


def make_log(log_id, timestamp, location="DOME-A", **extra):
    row = {
        "log_id": log_id,
        "timestamp": timestamp,
        "location": location,
        "ice_core_data": {"depth_meters": 120.0, "ice_temperature": -31.5},
        "transcript": [],
        "climate_events": [],
    }
    row.update(extra)
    return row


def make_depth_record(log_id, depth, timestamp="2026-06-02T08:00:00"):
    return {
        "log_id": log_id,
        "depth_meters": depth,
        "ice_temperature": -30.0,
        "bubble_density": 0.8,
        "co2_concentration": 270.0,
        "methane_concentration": 650.0,
        "oxygen18_ratio": -41.0,
        "dust_concentration": 10.0,
        "timestamp": timestamp,
    }


# --- 需求 1：回写正文不得重置回传三列，也不得重新进入待回传队列 ---

def test_resave_preserves_transmission_state(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-01T08:00:00"))
    db.update_transmission_status(
        "LOG_0001", True,
        transmission_time=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        error_message=None,
    )
    # 摘要跑完后回写同一条 log_id
    db.save_drilling_log(make_log(
        "LOG_0001", "2026-06-01T08:00:00",
        summary="摘要", sample_allocation={"A": 1},
    ))
    got = db.get_drilling_log("LOG_0001")
    assert got["transmitted"] is True
    assert got["transmission_time"] == "2026-06-01T09:00:00.000000+00:00"
    assert got["error_message"] is None
    assert got["summary"] == "摘要"
    assert got["sample_allocation"] == {"A": 1}
    assert db.get_pending_transmissions() == []


def test_new_log_uses_caller_transmission_values(db):
    db.save_drilling_log(make_log(
        "LOG_0001", "2026-06-01T08:00:00",
        transmitted=True,
        transmission_time="2026-06-01T09:00:00Z",
        error_message="retry once",
    ))
    got = db.get_drilling_log("LOG_0001")
    assert got["transmitted"] is True
    assert got["transmission_time"] == "2026-06-01T09:00:00.000000+00:00"
    assert got["error_message"] == "retry once"
    assert db.get_pending_transmissions() == []


# --- 需求 2：timestamp 归一化，字典序等于时间序，列表严格倒序 ---

def test_timestamp_normalized_to_fixed_width_utc(db):
    db.save_drilling_log(make_log(
        "LOG_0001", datetime(2026, 6, 2, 8, 0, 1, 123456, tzinfo=timezone.utc),
    ))
    got = db.get_drilling_log("LOG_0001")
    assert got["timestamp"] == "2026-06-02T08:00:01.123456+00:00"


@pytest.mark.parametrize("raw", [
    "2026-06-02T08:00:00",
    "2026-06-02 08:00:00",
    "2026-06-02T08:00:00.000000",
    "2026-06-02T08:00:00Z",
    "2026-06-02T16:00:00+08:00",
])
def test_timestamp_input_variants_all_normalize(db, raw):
    db.save_drilling_log(make_log("LOG_0001", raw))
    got = db.get_drilling_log("LOG_0001")
    assert got["timestamp"] == "2026-06-02T08:00:00.000000+00:00"


def test_get_all_drilling_logs_strictly_descending(db):
    # 乱序写入，且混用输入格式；今天下午的记录必须排在昨天晚上前面
    db.save_drilling_log(make_log("LOG_OLD", "2026-06-01 21:30:00"))
    db.save_drilling_log(make_log("LOG_NEW", "2026-06-02T15:04:00+08:00"))
    db.save_drilling_log(make_log("LOG_MID", "2026-06-02T03:00:00.5Z"))
    rows = db.get_all_drilling_logs()
    assert [row["log_id"] for row in rows] == ["LOG_NEW", "LOG_MID", "LOG_OLD"]
    timestamps = [row["timestamp"] for row in rows]
    assert timestamps == sorted(timestamps, reverse=True)


# --- 需求 3：深度序列分组连续、翻页、计数、空 log_id 不当成查全部 ---

def test_depth_series_groups_contiguous_and_sorted(db):
    for log_id, depths in (("LOG_A", (30.0, 10.0)), ("LOG_B", (5.0, 1.0))):
        for depth in depths:
            db.save_ice_core_depth_record(make_depth_record(log_id, depth))
    rows = db.get_ice_core_depth_series()
    assert [(row["log_id"], row["depth_meters"]) for row in rows] == [
        ("LOG_A", 10.0), ("LOG_A", 30.0),
        ("LOG_B", 1.0), ("LOG_B", 5.0),
    ]


def test_depth_series_offset_pagination_and_count(db):
    for depth in (1.0, 2.0, 3.0, 4.0, 5.0):
        db.save_ice_core_depth_record(make_depth_record("LOG_A", depth))
    db.save_ice_core_depth_record(make_depth_record("LOG_B", 9.0))

    assert db.count_ice_core_depth_records() == 6
    assert db.count_ice_core_depth_records("LOG_A") == 5

    page1 = db.get_ice_core_depth_series("LOG_A", limit=2)
    page2 = db.get_ice_core_depth_series("LOG_A", limit=2, offset=2)
    page3 = db.get_ice_core_depth_series("LOG_A", limit=2, offset=4)
    assert [row["depth_meters"] for row in page1] == [1.0, 2.0]
    assert [row["depth_meters"] for row in page2] == [3.0, 4.0]
    assert [row["depth_meters"] for row in page3] == [5.0]

    all_pages = db.get_ice_core_depth_series(limit=2, offset=4)
    assert [(row["log_id"], row["depth_meters"]) for row in all_pages] == [
        ("LOG_A", 5.0), ("LOG_B", 9.0),
    ]


def test_depth_series_empty_log_id_is_not_all(db):
    db.save_ice_core_depth_record(make_depth_record("LOG_A", 1.0))
    assert db.get_ice_core_depth_series("") == []
    assert db.count_ice_core_depth_records("") == 0


# --- 需求 4：读路径容忍 NULL / 空串的 JSON 列 ---

def test_read_tolerates_null_and_empty_json_columns(db, tmp_path):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-01T08:00:00"))
    conn = sqlite3.connect(str(tmp_path / "ice_core.db"))
    try:
        conn.execute(
            "UPDATE drilling_logs SET ice_core_data = NULL, transcript = '', "
            "climate_events = NULL, sample_allocation = '' WHERE log_id = ?",
            ("LOG_0001",),
        )
        conn.commit()
    finally:
        conn.close()

    got = db.get_drilling_log("LOG_0001")
    assert got["ice_core_data"] is None
    assert got["transcript"] is None
    assert got["climate_events"] is None
    assert got["sample_allocation"] is None
    assert got["log_id"] == "LOG_0001"
    assert got["location"] == "DOME-A"

    rows = db.get_all_drilling_logs()
    assert len(rows) == 1
    assert rows[0]["ice_core_data"] is None
    assert rows[0]["transcript"] is None
    assert rows[0]["climate_events"] is None
    assert rows[0]["sample_allocation"] is None


# --- 需求 5：models.py 约束，违规抛 ValidationError，不静默夹紧 ---

def _valid_segment_kwargs(**overrides):
    kwargs = {
        "speaker": "SPEAKER_00", "speaker_role": "engineer",
        "start_time": 0.0, "end_time": 4.5, "text": "进尺", "confidence": 0.9,
    }
    kwargs.update(overrides)
    return kwargs


def _valid_log_kwargs(**overrides):
    kwargs = {
        "log_id": "LOG_0001",
        "timestamp": "2026-06-02T08:00:00",
        "location": "DOME-A",
        "ice_core_data": IceCoreData(
            depth_meters=120.0, ice_temperature=-31.5, bubble_density=0.82,
            co2_concentration=268.0, methane_concentration=640.0,
            oxygen18_ratio=-41.2, dust_concentration=12.5,
        ),
        "transcript": [],
        "climate_events": [],
    }
    kwargs.update(overrides)
    return kwargs


def test_depth_meters_must_be_non_negative():
    with pytest.raises(ValidationError):
        IceCoreData(
            depth_meters=-3.5, ice_temperature=-31.5, bubble_density=0.82,
            co2_concentration=268.0, methane_concentration=640.0,
            oxygen18_ratio=-41.2, dust_concentration=12.5,
        )


def test_confidence_must_be_within_unit_interval():
    with pytest.raises(ValidationError):
        TranscriptSegment(**_valid_segment_kwargs(confidence=1.7))
    with pytest.raises(ValidationError):
        TranscriptSegment(**_valid_segment_kwargs(confidence=-0.1))
    with pytest.raises(ValidationError):
        ClimateEvent(
            event_type="火山灰层", description="肉眼可见火山灰", confidence=1.7,
        )
    # 边界值合法
    assert TranscriptSegment(**_valid_segment_kwargs(confidence=0.0)).confidence == 0.0
    assert TranscriptSegment(**_valid_segment_kwargs(confidence=1.0)).confidence == 1.0


def test_end_time_must_not_precede_start_time():
    with pytest.raises(ValidationError):
        TranscriptSegment(**_valid_segment_kwargs(start_time=5.0, end_time=4.5))
    assert TranscriptSegment(
        **_valid_segment_kwargs(start_time=4.5, end_time=4.5)
    ).end_time == 4.5


@pytest.mark.parametrize("bad_log_id", ["", "   ", "\t\n"])
def test_log_id_must_not_be_blank(bad_log_id):
    with pytest.raises(ValidationError):
        DrillingLog(**_valid_log_kwargs(log_id=bad_log_id))


# --- 需求 6：update_transmission_status 自动补时间戳，datetime 不直接绑给 sqlite3 ---

def test_update_transmission_status_fills_timestamp(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-01T08:00:00"))
    db.update_transmission_status("LOG_0001", True)
    got = db.get_drilling_log("LOG_0001")
    assert got["transmitted"] is True
    assert isinstance(got["transmission_time"], str)
    assert got["transmission_time"]


def test_update_transmission_status_accepts_datetime(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-01T08:00:00"))
    db.update_transmission_status(
        "LOG_0001", True,
        transmission_time=datetime(2026, 6, 1, 9, 30, tzinfo=timezone.utc),
    )
    got = db.get_drilling_log("LOG_0001")
    assert got["transmission_time"] == "2026-06-01T09:30:00.000000+00:00"


# --- 需求 7：测试不在仓库目录留下 .db 文件 ---

def test_no_db_files_left_in_repo():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert glob.glob(os.path.join(repo_root, "**", "*.db"), recursive=True) == []
