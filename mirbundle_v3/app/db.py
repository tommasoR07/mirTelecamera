from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent / 'data' / 'mir_web_app.db'

DEFAULT_SETTINGS = {
    'host': '',
    'username': '',
    'password': '',
    'mission_group_id': '',
    'drive_endpoint_path': '',
    'drive_http_method': 'POST',
    'drive_body_template': '{"linear": {{linear}}, "angular": {{angular}}}',
    'camera_stream_url': '',
    'camera_snapshot_url': '',
    'manual_take_endpoint_path': '',
    'manual_take_http_method': 'PUT',
    'manual_take_body_template': '{"mode_id": 1}',
    'manual_release_endpoint_path': '',
    'manual_release_http_method': 'PUT',
    'manual_release_body_template': '{"state_id": 3}',
    'tracking_target_size_percent': '32',
    'tracking_max_linear': '1.50',
    'tracking_max_angular': '1.50',
}




def _ensure_column(conn: sqlite3.Connection, table: str, name: str, sql_type: str, default_sql: str) -> None:
    cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    if name not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type} NOT NULL DEFAULT {default_sql}')


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                host TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
                password TEXT NOT NULL DEFAULT '',
                mission_group_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO settings (id, host, username, password, mission_group_id)
            VALUES (1, '', '', '', '')
            """
        )
        _ensure_column(conn, 'settings', 'drive_endpoint_path', 'TEXT', "''")
        _ensure_column(conn, 'settings', 'drive_http_method', 'TEXT', "'POST'")
        _ensure_column(conn, 'settings', 'drive_body_template', 'TEXT', "'{\"linear\": {{linear}}, \"angular\": {{angular}}}'")
        _ensure_column(conn, 'settings', 'camera_stream_url', 'TEXT', "''")
        _ensure_column(conn, 'settings', 'camera_snapshot_url', 'TEXT', "''")
        _ensure_column(conn, 'settings', 'manual_take_endpoint_path', 'TEXT', "''")
        _ensure_column(conn, 'settings', 'manual_take_http_method', 'TEXT', "'PUT'")
        _ensure_column(conn, 'settings', 'manual_take_body_template', 'TEXT', "'{\"mode_id\": 1}'")
        _ensure_column(conn, 'settings', 'manual_release_endpoint_path', 'TEXT', "''")
        _ensure_column(conn, 'settings', 'manual_release_http_method', 'TEXT', "'PUT'")
        _ensure_column(conn, 'settings', 'manual_release_body_template', 'TEXT', "'{\"state_id\": 3}'")
        _ensure_column(conn, 'settings', 'tracking_target_size_percent', 'TEXT', "'32'")
        _ensure_column(conn, 'settings', 'tracking_max_linear', 'TEXT', "'1.50'")
        _ensure_column(conn, 'settings', 'tracking_max_angular', 'TEXT', "'1.50'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                steps_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_settings() -> dict:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM settings WHERE id = 1').fetchone()
        data = dict(row) if row else DEFAULT_SETTINGS.copy()
        for key, value in DEFAULT_SETTINGS.items():
            data.setdefault(key, value)
        data['camera_stream_url'] = data.get('camera_stream_url') or DEFAULT_SETTINGS['camera_stream_url']
        data['camera_snapshot_url'] = data.get('camera_snapshot_url') or DEFAULT_SETTINGS['camera_snapshot_url']
        return data


def save_settings(
    host: str,
    username: str,
    password: str,
    mission_group_id: str,
    drive_endpoint_path: str,
    drive_http_method: str,
    drive_body_template: str,
    camera_stream_url: str,
    camera_snapshot_url: str,
    manual_take_endpoint_path: str = '',
    manual_take_http_method: str = 'PUT',
    manual_take_body_template: str = '{"mode_id": 1}',
    manual_release_endpoint_path: str = '',
    manual_release_http_method: str = 'PUT',
    manual_release_body_template: str = '{"state_id": 3}',
    tracking_target_size_percent: str = '32',
    tracking_max_linear: str = '1.50',
    tracking_max_angular: str = '1.50',
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE settings
            SET host = ?, username = ?, password = ?, mission_group_id = ?,
                drive_endpoint_path = ?, drive_http_method = ?, drive_body_template = ?,
                camera_stream_url = ?, camera_snapshot_url = ?,
                manual_take_endpoint_path = ?, manual_take_http_method = ?, manual_take_body_template = ?,
                manual_release_endpoint_path = ?, manual_release_http_method = ?, manual_release_body_template = ?,
                tracking_target_size_percent = ?, tracking_max_linear = ?, tracking_max_angular = ?
            WHERE id = 1
            """,
            (
                host.strip(),
                username.strip(),
                password,
                mission_group_id.strip(),
                drive_endpoint_path.strip().lstrip('/'),
                drive_http_method.strip().upper() or 'POST',
                drive_body_template.strip() or DEFAULT_SETTINGS['drive_body_template'],
                camera_stream_url.strip() or DEFAULT_SETTINGS['camera_stream_url'],
                camera_snapshot_url.strip() or DEFAULT_SETTINGS['camera_snapshot_url'],
                manual_take_endpoint_path.strip().lstrip('/'),
                manual_take_http_method.strip().upper() or 'PUT',
                manual_take_body_template.strip() or DEFAULT_SETTINGS['manual_take_body_template'],
                manual_release_endpoint_path.strip().lstrip('/'),
                manual_release_http_method.strip().upper() or 'PUT',
                manual_release_body_template.strip() or DEFAULT_SETTINGS['manual_release_body_template'],
                tracking_target_size_percent.strip() or DEFAULT_SETTINGS['tracking_target_size_percent'],
                tracking_max_linear.strip() or DEFAULT_SETTINGS['tracking_max_linear'],
                tracking_max_angular.strip() or DEFAULT_SETTINGS['tracking_max_angular'],
            ),
        )


def list_workflows() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM workflows ORDER BY id DESC').fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['steps'] = json.loads(item.pop('steps_json'))
            items.append(item)
        return items


def get_workflow(workflow_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM workflows WHERE id = ?', (workflow_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item['steps'] = json.loads(item.pop('steps_json'))
        return item


def create_workflow(name: str, description: str, steps: list[dict]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO workflows (name, description, steps_json) VALUES (?, ?, ?)',
            (name.strip(), description.strip(), json.dumps(steps)),
        )
        return int(cur.lastrowid)


def delete_workflow(workflow_id: int) -> None:
    with get_conn() as conn:
        conn.execute('DELETE FROM workflows WHERE id = ?', (workflow_id,))
