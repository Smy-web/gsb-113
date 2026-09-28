"""现有测试：只覆盖正常路径。跑通说明台账的基本读写没坏。"""
import pytest

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
        "transcript": [
            {
                "speaker": "SPEAKER_00",
                "speaker_role": "engineer",
                "start_time": 0.0,
                "end_time": 4.5,
                "text": "本回次进尺 3.2 米",
                "confidence": 0.88,
            }
        ],
        "climate_events": [],
    }
    row.update(extra)
    return row


def test_save_then_read_roundtrip(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-02T08:00:00"))
    got = db.get_drilling_log("LOG_0001")
    assert got is not None
    assert got["log_id"] == "LOG_0001"
    assert got["location"] == "DOME-A"
    assert got["ice_core_data"]["depth_meters"] == 120.0
    assert got["transmitted"] is False
    assert got["sample_allocation"] is None


def test_missing_log_returns_none(db):
    assert db.get_drilling_log("LOG_NOPE") is None


def test_pending_queue_holds_unsent_logs_oldest_first(db):
    db.save_drilling_log(make_log("LOG_0001", "2026-06-01T08:00:00"))
    db.save_drilling_log(make_log("LOG_0002", "2026-06-02T08:00:00"))
    pending = db.get_pending_transmissions()
    assert [row["log_id"] for row in pending] == ["LOG_0001", "LOG_0002"]
    assert set(pending[0]) == {
        "log_id", "timestamp", "location", "summary", "sample_allocation",
    }


def test_depth_series_one_log_ascending(db):
    for depth in (30.0, 10.0, 20.0):
        db.save_ice_core_depth_record({
            "log_id": "LOG_0001",
            "depth_meters": depth,
            "ice_temperature": -30.0,
            "bubble_density": 0.8,
            "co2_concentration": 270.0,
            "methane_concentration": 650.0,
            "oxygen18_ratio": -41.0,
            "dust_concentration": 10.0,
            "timestamp": "2026-06-02T08:00:00",
        })
    rows = db.get_ice_core_depth_series("LOG_0001")
    assert [row["depth_meters"] for row in rows] == [10.0, 20.0, 30.0]


def test_models_accept_valid_log():
    log = DrillingLog(
        log_id="LOG_0001",
        timestamp="2026-06-02T08:00:00",
        location="DOME-A",
        ice_core_data=IceCoreData(
            depth_meters=120.0, ice_temperature=-31.5, bubble_density=0.82,
            co2_concentration=268.0, methane_concentration=640.0,
            oxygen18_ratio=-41.2, dust_concentration=12.5, estimated_age=1520.0,
        ),
        transcript=[TranscriptSegment(
            speaker="SPEAKER_00", speaker_role="engineer",
            start_time=0.0, end_time=4.5, text="进尺 3.2 米", confidence=0.88,
        )],
        climate_events=[ClimateEvent(
            event_type="火山灰层", description="肉眼可见火山灰",
            estimated_year=1450, depth_reference=118.0, confidence=0.72,
        )],
    )
    assert log.transmitted is False
    assert log.ice_core_data.depth_meters == 120.0
