"""Small JSON business values in one plugin's own data directory."""

from contextlib import closing, contextmanager
import json
from pathlib import Path

from pydantic import ConfigDict, JsonValue, TypeAdapter

from ..storage.sqlite import connect


JSON = TypeAdapter(JsonValue, config=ConfigDict(strict=True, allow_inf_nan=False))
APPLICATION_ID = 0x4C424B56  # "LBKV"
FORMAT_VERSION = 1


class PluginKV:
    def __init__(self, directory: Path):
        self.path = directory / "kv.sqlite3"

    @contextmanager
    def connection(self, key: str):
        if not key.strip():
            raise ValueError("插件 KV key 不能为空")
        with closing(connect(self.path)) as db, db:
            identity = (db.execute("PRAGMA application_id").fetchone()[0], db.execute("PRAGMA user_version").fetchone()[0])
            if identity == (0, 0):
                # A new file, or one written before the format was marked; the table shape is the same.
                db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={FORMAT_VERSION}")
            elif identity != (APPLICATION_ID, FORMAT_VERSION):
                raise ValueError(f"{self.path} 不是当前格式的插件 KV：application_id={identity[0]}, format={identity[1]}")
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
