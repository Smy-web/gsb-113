"""回归测试：钉住铱星重复回传、列表乱序、深度曲线断线、非法数据与 NULL 容忍。"""
import re
import sqlite3
from datetime import datetime

import pytest
from pydantic import ValidationError

from backend import database
from backend.models import ClimateEvent, DrillingLog, IceCoreData, TranscriptSegment


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
        "ice_core_data": {
            "depth_meters": 120.0,
            "ice_temperature": -31.5,
            "bubble_density": 0.82,
            "co2_concentration": 268.0,
            "methane_concentration": 640.0,
            "oxygen18_ratio": -41.2,
            "dust_concentration": 12.5,
            "estimated_age": 1520.0,
        },
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


def test_resave_preserves_transmission_state_and_pending_queue(db):
    # 负例锚点：删掉 save_drilling_log 里「先读既有回传状态」的判断，本条应变红。
    db.save_drilling_log(make_log("LOG_0001", "2026-06-02T08:00:00"))
    db.update_transmission_status(
        "LOG_0001", True,
        transmission_time="2026-06-03T01:00:00",
        error_message=None,
    )
    # 摘要跑完后回写同一条 log_id，只补 summary / sample_allocation。
    db.save_drilling_log(make_log(
        "LOG_0001", "2026-06-02T08:00:00",
        summary="回次正常", sample_allocation={"A": 1},
    ))
    got = db.get_drilling_log("LOG_0001")
    assert got["transmitted"] is True
    assert got["transmission_time"] == "2026-06-03T01:00:00.000000"
    assert got["error_message"] is None
    assert got["summary"] == "回次正常"
    assert got["sample_allocation"] == {"A": 1}
    assert db.get_pending_transmissions() == []


def test_timestamp_normalized_and_all_logs_strictly_descending(db):
    db.save_drilling_log(make_log("LOG_A", "2026-06-02 08:00:00"))           #  naive -> UTC
    db.save_drilling_log(make_log("LOG_B", "2026-06-02T17:00:00+08:00"))     #  09:00 UTC
    db.save_drilling_log(make_log("LOG_C", datetime(2026, 6, 2, 10, 30, 0)))
    db.save_drilling_log(make_log("LOG_D", "2026-06-02T07:00:00.5Z"))
    db.save_drilling_log(make_log("LOG_E", "2026-06-02T08:00:00.123456"))

    rows = db.get_all_drilling_logs()
    assert [row["log_id"] for row in rows] == ["LOG_C", "LOG_B", "LOG_E", "LOG_A", "LOG_D"]

    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$")
    stored = [row["timestamp"] for row in rows]
    assert all(isinstance(ts, str) and pattern.match(ts) for ts in stored)
    assert stored == sorted(stored, reverse=True)  # 字典序 == 时间序
    assert db.get_drilling_log("LOG_B")["timestamp"] == "2026-06-02T09:00:00.000000"
    assert db.get_drilling_log("LOG_D")["timestamp"] == "2026-06-02T07:00:00.500000"


def test_depth_series_grouped_paginated_counted_and_empty_log_id(db):
    for depth in (30.0, 10.0, 20.0):
        db.save_ice_core_depth_record(make_depth_record("LOG_0001", depth))
    for depth in (5.0, 15.0):
        db.save_ice_core_depth_record(make_depth_record("LOG_0002", depth))

    rows = db.get_ice_core_depth_series()
    assert [(r["log_id"], r["depth_meters"]) for r in rows] == [
        ("LOG_0001", 10.0), ("LOG_0001", 20.0), ("LOG_0001", 30.0),
        ("LOG_0002", 5.0), ("LOG_0002", 15.0),
    ]

    page1 = db.get_ice_core_depth_series(limit=2, offset=0)
    page2 = db.get_ice_core_depth_series(limit=2, offset=2)
    page3 = db.get_ice_core_depth_series(limit=2, offset=4)
    assert [r["id"] for r in page1 + page2 + page3] == [r["id"] for r in rows]
    assert db.count_ice_core_depth_records() == 5
    assert db.count_ice_core_depth_records("LOG_0001") == 3

    one_log = db.get_ice_core_depth_series("LOG_0002")
    assert [r["depth_meters"] for r in one_log] == [5.0, 15.0]
    assert db.get_ice_core_depth_series("") == []  # 空串不得静默当成查全部


def test_read_path_tolerates_null_json_columns(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-02T08:00:00"))
    conn = sqlite3.connect(database.DB_PATH)
    try:
        conn.execute(
            "UPDATE drilling_logs SET ice_core_data = NULL, transcript = '', "
            "climate_events = NULL, sample_allocation = NULL WHERE log_id = ?",
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
    assert got["location"] == "DOME-A"

    listed = db.get_all_drilling_logs()
    assert listed[0]["ice_core_data"] is None
    assert listed[0]["transcript"] is None
    assert listed[0]["climate_events"] is None


def test_models_reject_invalid_values():
    base_ice = dict(
        depth_meters=120.0, ice_temperature=-31.5, bubble_density=0.82,
        co2_concentration=268.0, methane_concentration=640.0,
        oxygen18_ratio=-41.2, dust_concentration=12.5,
    )
    with pytest.raises(ValidationError):
        IceCoreData(**{**base_ice, "depth_meters": -3.5})

    segment = dict(speaker="SPEAKER_00", speaker_role="engineer",
                   start_time=0.0, end_time=4.5, text="进尺", confidence=0.9)
    with pytest.raises(ValidationError):
        TranscriptSegment(**{**segment, "confidence": 1.7})
    with pytest.raises(ValidationError):
        ClimateEvent(event_type="火山灰层", description="灰", confidence=-0.1)
    with pytest.raises(ValidationError):
        TranscriptSegment(**{**segment, "start_time": 5.0, "end_time": 4.5})

    log_kwargs = dict(
        timestamp="2026-06-02T08:00:00", location="DOME-A",
        ice_core_data=IceCoreData(**base_ice),
        transcript=[], climate_events=[],
    )
    with pytest.raises(ValidationError):
        DrillingLog(log_id="", **log_kwargs)
    with pytest.raises(ValidationError):
        DrillingLog(log_id="   ", **log_kwargs)


def test_update_transmission_status_fills_time_and_stores_text(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-02T08:00:00"))
    db.update_transmission_status("LOG_0001", True)
    got = db.get_drilling_log("LOG_0001")
    assert got["transmitted"] is True
    assert isinstance(got["transmission_time"], str) and got["transmission_time"]

    db.save_drilling_log(make_log("LOG_0002", "2026-06-02T08:00:00"))
    db.update_transmission_status(
        "LOG_0002", True,
        transmission_time=datetime(2026, 6, 3, 1, 2, 3),
    )
    got2 = db.get_drilling_log("LOG_0002")
    assert got2["transmission_time"] == "2026-06-03T01:02:03.000000"
