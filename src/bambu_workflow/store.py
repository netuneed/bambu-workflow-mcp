from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

TERMINAL = {"completed", "canceled", "failed"}
DISPATCHED = {"start_sent", "start_uncertain", "accepted", "preparing", "printing", "paused", "completed", "canceled"}


class _Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "jobs.sqlite3"
        with self.connect() as con:
            con.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,
                printer TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS leases(printer TEXT PRIMARY KEY,job TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS beds(printer TEXT PRIMARY KEY,token TEXT NOT NULL,
                expires REAL NOT NULL,identity TEXT NOT NULL,consumed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                job TEXT NOT NULL,state TEXT NOT NULL,time REAL NOT NULL,detail TEXT NOT NULL);
            """)

    def connect(self):
        con = sqlite3.connect(self.db, timeout=20,factory=_Connection)
        con.row_factory = sqlite3.Row
        return con

    @contextlib.contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _row(row):
        if row is None:
            raise ValueError("Unknown job id.")
        return dict(id=row["id"], printer_id=row["printer"], state=row["state"],
                    updated_at=row["updated"], **json.loads(row["data"]))

    def create(self, request_key: str, printer: str, payload: dict) -> tuple[dict, bool]:
        if not request_key or len(request_key) > 180:
            raise ValueError("request_id must be a stable nonempty key of at most 180 characters.")
        with self.transaction() as con:
            old = con.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if old:
                job = self._row(old)
                if job["printer_id"] != printer or job["request"] != payload:
                    raise ValueError("request_id already belongs to a different request. Reuse its original arguments.")
                return job, False
            jid = uuid.uuid4().hex
            data = {"request": payload, "created_at": time.time(), "progress": 0}
            con.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (jid, request_key, printer, "queued", json.dumps(data), time.time()))
            con.execute("INSERT INTO events(job,state,time,detail) VALUES(?,?,?,?)", (jid,"queued",time.time(),"{}"))
        return self.get(jid), True

    def get(self, jid: str) -> dict:
        with self.connect() as con:
            return self._row(con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())

    def list(self, limit=30) -> list:
        with self.connect() as con:
            return [self._row(r) for r in con.execute("SELECT * FROM jobs ORDER BY updated DESC LIMIT ?", (limit,))]

    def update(self, jid: str, state: str | None = None, **changes) -> dict:
        with self.transaction() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            job = self._row(row)
            data = json.loads(row["data"])
            data.update(changes)
            state = state or row["state"]
            con.execute("UPDATE jobs SET state=?,data=?,updated=? WHERE id=?", (state,json.dumps(data,ensure_ascii=False),time.time(),jid))
            if state != job["state"] or ("error" in changes and changes["error"] != job.get("error")):
                con.execute("INSERT INTO events(job,state,time,detail) VALUES(?,?,?,?)", (jid,state,time.time(),json.dumps(changes,ensure_ascii=False)))
            if state in TERMINAL:
                con.execute("DELETE FROM leases WHERE job=?", (jid,))
        return self.get(jid)

    def events(self, jid: str):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT state,time,detail FROM events WHERE job=? ORDER BY id", (jid,))]

    def acquire(self, jid: str):
        job = self.get(jid)
        with self.transaction() as con:
            row = con.execute("SELECT job FROM leases WHERE printer=?", (job["printer_id"],)).fetchone()
            if row and row[0] != jid:
                raise ValueError("Another persistent job owns this printer. Reconcile it before a new launch.")
            con.execute("INSERT OR IGNORE INTO leases VALUES(?,?)", (job["printer_id"],jid))

    def release(self, jid: str):
        with self.transaction() as con:
            con.execute("DELETE FROM leases WHERE job=?", (jid,))

    def leased_job(self, printer: str):
        with self.connect() as con:
            row = con.execute("SELECT job FROM leases WHERE printer=?", (printer,)).fetchone()
            return row[0] if row else None

    def bed_ready(self, printer: str, identity: str, valid_for=1800):
        token = uuid.uuid4().hex
        with self.transaction() as con:
            con.execute("INSERT OR REPLACE INTO beds VALUES(?,?,?,?,0)", (printer,token,time.time()+valid_for,identity))
        return {"printer_id": printer, "bed_ready": True, "expires_at": time.time()+valid_for,
                "conditions": "One start only; expires if printer task identity changes or 30 minutes elapse."}

    def validate_bed(self, printer: str, identity: str):
        with self.connect() as con:
            bed=con.execute("SELECT * FROM beds WHERE printer=?",(printer,)).fetchone()
            if not bed or bed["consumed"] or bed["expires"] < time.time() or bed["identity"] != identity:
                raise ValueError("Bed readiness is missing or expired. Confirm the physical bed is empty and installed.")

    def consume_bed_and_record_start(self, jid: str, identity: str, intent: dict):
        with self.transaction() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            job = self._row(row)
            if job["state"] in DISPATCHED:
                raise ValueError("Start was already attempted. Reconcile this job; never send it twice.")
            bed = con.execute("SELECT * FROM beds WHERE printer=?", (job["printer_id"],)).fetchone()
            if not bed or bed["consumed"] or bed["expires"] < time.time() or bed["identity"] != identity:
                raise ValueError("Bed readiness is missing or expired. Confirm the physical bed is empty and installed.")
            lease = con.execute("SELECT job FROM leases WHERE printer=?",(job["printer_id"],)).fetchone()
            if not lease or lease[0] != jid:
                raise ValueError("Printer launch lease is not owned by this job.")
            data = json.loads(row["data"])
            data.update(start_intent=intent, progress=85)
            con.execute("UPDATE beds SET consumed=1 WHERE printer=?", (job["printer_id"],))
            con.execute("UPDATE jobs SET state='start_sent',data=?,updated=? WHERE id=?",(json.dumps(data),time.time(),jid))
            con.execute("INSERT INTO events(job,state,time,detail) VALUES(?,?,?,?)",(jid,"start_sent",time.time(),json.dumps(intent)))

