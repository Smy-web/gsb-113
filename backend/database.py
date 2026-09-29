import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'ice_core.db')

_TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%S.%f+00:00'


def _normalize_timestamp(value):
    """归一化时间戳：统一存成定宽的 UTC ISO-8601 文本，使字典序等于时间序。

    接受 datetime 对象或 ISO-8601 文本（T 或空格分隔、可带微秒、
    可带 Z / 偏移量 / 不带时区）；不带时区的输入按 UTC 处理。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(('Z', 'z')):
            text = text[:-1] + '+00:00'
        parsed = datetime.fromisoformat(text)
    else:
        raise TypeError(f'unsupported timestamp type: {type(value)!r}')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def _json_loads_or_none(text):
    """容忍 NULL / 空串的 JSON 读取，读作 None。"""
    if text is None or text == '':
        return None
    return json.loads(text)


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS drilling_logs (
                log_id TEXT PRIMARY KEY,
                timestamp DATETIME,
                location TEXT,
                ice_core_data TEXT,
                transcript TEXT,
                climate_events TEXT,
                summary TEXT,
                sample_allocation TEXT,
                transmitted BOOLEAN DEFAULT 0,
                transmission_time DATETIME,
                error_message TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audio_uploads (
                audio_id TEXT PRIMARY KEY,
                log_id TEXT,
                upload_time DATETIME,
                original_path TEXT,
                processed_path TEXT,
                status TEXT,
                FOREIGN KEY (log_id) REFERENCES drilling_logs(log_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ice_core_depth_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_id TEXT,
                depth_meters REAL,
                ice_temperature REAL,
                bubble_density REAL,
                co2_concentration REAL,
                methane_concentration REAL,
                oxygen18_ratio REAL,
                dust_concentration REAL,
                estimated_age REAL,
                timestamp DATETIME,
                FOREIGN KEY (log_id) REFERENCES drilling_logs(log_id)
            )
        ''')
        
        conn.commit()


def save_drilling_log(log: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 先读出这条日志已有的回传状态：对已存在的 log_id 回写正文/摘要时，
        # transmitted / transmission_time / error_message 三列必须保持原值，
        # 避免已回传的记录被重置后重新进入待回传队列。
        cursor.execute(
            'SELECT 1 FROM drilling_logs WHERE log_id = ?', (log['log_id'],)
        )
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute('''
                UPDATE drilling_logs
                SET timestamp = ?, location = ?, ice_core_data = ?, transcript = ?,
                    climate_events = ?, summary = ?, sample_allocation = ?
                WHERE log_id = ?
            ''', (
                _normalize_timestamp(log['timestamp']),
                log['location'],
                json.dumps(log['ice_core_data']),
                json.dumps(log['transcript']),
                json.dumps(log['climate_events']),
                log.get('summary'),
                json.dumps(log.get('sample_allocation')) if log.get('sample_allocation') else None,
                log['log_id'],
            ))
        else:
            cursor.execute('''
                INSERT INTO drilling_logs
                (log_id, timestamp, location, ice_core_data, transcript, climate_events,
                 summary, sample_allocation, transmitted, transmission_time, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                log['log_id'],
                _normalize_timestamp(log['timestamp']),
                log['location'],
                json.dumps(log['ice_core_data']),
                json.dumps(log['transcript']),
                json.dumps(log['climate_events']),
                log.get('summary'),
                json.dumps(log.get('sample_allocation')) if log.get('sample_allocation') else None,
                log.get('transmitted', False),
                _normalize_timestamp(log.get('transmission_time')),
                log.get('error_message')
            ))
        conn.commit()


def get_drilling_log(log_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM drilling_logs WHERE log_id = ?', (log_id,))
        row = cursor.fetchone()
        if row:
            return {
                'log_id': row['log_id'],
                'timestamp': row['timestamp'],
                'location': row['location'],
                'ice_core_data': _json_loads_or_none(row['ice_core_data']),
                'transcript': _json_loads_or_none(row['transcript']),
                'climate_events': _json_loads_or_none(row['climate_events']),
                'summary': row['summary'],
                'sample_allocation': _json_loads_or_none(row['sample_allocation']),
                'transmitted': bool(row['transmitted']),
                'transmission_time': row['transmission_time'],
                'error_message': row['error_message']
            }
    return None


def get_all_drilling_logs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM drilling_logs ORDER BY timestamp DESC LIMIT ?', (limit,))
        rows = cursor.fetchall()
        return [{
            'log_id': row['log_id'],
            'timestamp': row['timestamp'],
            'location': row['location'],
            'ice_core_data': _json_loads_or_none(row['ice_core_data']),
            'transcript': _json_loads_or_none(row['transcript']),
            'climate_events': _json_loads_or_none(row['climate_events']),
            'summary': row['summary'],
            'sample_allocation': _json_loads_or_none(row['sample_allocation']),
            'transmitted': bool(row['transmitted']),
            'transmission_time': row['transmission_time'],
            'error_message': row['error_message']
        } for row in rows]


def save_ice_core_depth_record(record: Dict[str, Any]) -> None:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ice_core_depth_series 
            (log_id, depth_meters, ice_temperature, bubble_density, co2_concentration,
             methane_concentration, oxygen18_ratio, dust_concentration, estimated_age, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record['log_id'],
            record['depth_meters'],
            record['ice_temperature'],
            record['bubble_density'],
            record['co2_concentration'],
            record['methane_concentration'],
            record['oxygen18_ratio'],
            record['dust_concentration'],
            record.get('estimated_age'),
            _normalize_timestamp(record['timestamp'])
        ))
        conn.commit()


def get_ice_core_depth_series(log_id: Optional[str] = None, limit: int = 500,
                              offset: int = 0) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if log_id is not None:
            cursor.execute(
                'SELECT * FROM ice_core_depth_series WHERE log_id = ? '
                'ORDER BY depth_meters ASC LIMIT ? OFFSET ?',
                (log_id, limit, offset)
            )
        else:
            # 不传 log_id 时：同一份日志的点连续排列，组内按深度升序。
            cursor.execute(
                'SELECT * FROM ice_core_depth_series '
                'ORDER BY log_id ASC, depth_meters ASC LIMIT ? OFFSET ?',
                (limit, offset)
            )
        rows = cursor.fetchall()
        return [{
            'id': row['id'],
            'log_id': row['log_id'],
            'depth_meters': row['depth_meters'],
            'ice_temperature': row['ice_temperature'],
            'bubble_density': row['bubble_density'],
            'co2_concentration': row['co2_concentration'],
            'methane_concentration': row['methane_concentration'],
            'oxygen18_ratio': row['oxygen18_ratio'],
            'dust_concentration': row['dust_concentration'],
            'estimated_age': row['estimated_age'],
            'timestamp': row['timestamp']
        } for row in rows]


def count_ice_core_depth_records(log_id: Optional[str] = None) -> int:
    """返回符合 log_id 过滤条件的深度记录总条数，供调用方判断有没有下一页。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if log_id is not None:
            cursor.execute(
                'SELECT COUNT(*) FROM ice_core_depth_series WHERE log_id = ?',
                (log_id,)
            )
        else:
            cursor.execute('SELECT COUNT(*) FROM ice_core_depth_series')
        return cursor.fetchone()[0]


def update_transmission_status(log_id: str, transmitted: bool, 
                               transmission_time: Optional[datetime] = None,
                               error_message: Optional[str] = None) -> None:
    if transmitted and transmission_time is None:
        transmission_time = datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drilling_logs 
            SET transmitted = ?, transmission_time = ?, error_message = ?
            WHERE log_id = ?
        ''', (transmitted, _normalize_timestamp(transmission_time), error_message, log_id))
        conn.commit()


def get_pending_transmissions() -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM drilling_logs WHERE transmitted = 0 ORDER BY timestamp ASC')
        rows = cursor.fetchall()
        return [{
            'log_id': row['log_id'],
            'timestamp': row['timestamp'],
            'location': row['location'],
            'summary': row['summary'],
            'sample_allocation': json.loads(row['sample_allocation']) if row['sample_allocation'] else None
        } for row in rows]
