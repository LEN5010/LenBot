"""Small JSON business values in one plugin's own data directory."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3

from pydantic import ConfigDict, JsonValue, TypeAdapter


JSON = TypeAdapter(JsonValue, config=ConfigDict(strict=True, allow_inf_nan=False))


class PluginKV:
    def __init__(self, directory: Path):
        self.path = directory / "kv.sqlite3"

    @contextmanager
    def connection(self, key: str):
        if not key.strip():
            raise ValueError("插件 KV key 不能为空")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            yield db

    def get(self, key: str, default: JsonValue) -> JsonValue:
        with self.connection(key) as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return default if row is None else JSON.validate_json(row[0])

    def set(self, key: str, value: JsonValue) -> None:
        data = json.dumps(JSON.validate_python(value), ensure_ascii=False, allow_nan=False)
        with self.connection(key) as db:
            db.execute("INSERT INTO kv(key,value) VALUES (?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, data))

    def delete(self, key: str) -> bool:
        with self.connection(key) as db:
            return db.execute("DELETE FROM kv WHERE key=?", (key,)).rowcount == 1
