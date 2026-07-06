import sqlite3
from datetime import datetime
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 2

_init_media_db_sql = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS media (
    afc_path TEXT NOT NULL,
    st_size INTEGER NOT NULL,
    st_mtime DATETIME NOT NULL,
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(afc_path, st_size, st_mtime)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class MediaDatabase:
    """SQLite database tracking which device media files have been imported,
    plus per-library settings (e.g. the filename layout).
    A file is identified by its device path, size and modification time."""

    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path)
        # the init script is idempotent, so upgrading a v0 database (0.1.x, media
        # table only) is just running it and bumping user_version
        self.conn.executescript(_init_media_db_sql)
        self._migrate_mtimes_to_epoch()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate_mtimes_to_epoch(self):
        # v1 and earlier stored st_mtime as local-wall-clock ISO strings, which
        # break dedup across a host timezone change; rewrite them as epoch seconds
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]

        if version >= 2:
            return

        rows = self.conn.execute("SELECT rowid, st_mtime FROM media").fetchall()

        for rowid, raw in rows:
            if not isinstance(raw, str):
                continue  # already numeric

            self.conn.execute(
                "UPDATE media SET st_mtime = ? WHERE rowid = ?",
                (datetime.fromisoformat(raw).timestamp(), rowid),
            )

    def contains(self, afc_path: PurePosixPath, st_size: int, st_mtime: datetime):
        """Return whether this exact file (path, size, mtime) was already imported."""

        cursor = self.conn.execute(
            "SELECT 1 FROM media WHERE afc_path = ? AND st_size = ? AND st_mtime = ? LIMIT 1",
            (str(afc_path), st_size, st_mtime.timestamp()),
        )

        return cursor.fetchone() is not None

    def record(self, afc_path: PurePosixPath, st_size: int, st_mtime: datetime):
        """Record a file as imported (no-op if already recorded)."""

        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO media (afc_path, st_size, st_mtime) VALUES (?, ?, ?)",
                (str(afc_path), st_size, st_mtime.timestamp()),
            )

    def get_setting(self, key: str):
        """Return the stored value for `key`, or None if unset."""

        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()

        return row[0] if row else None

    def set_setting(self, key: str, value: str):
        """Store `value` under `key`, overwriting any previous value."""

        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def close(self):
        self.conn.close()
