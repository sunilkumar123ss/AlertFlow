#!/usr/bin/env python3
"""Simulate a SQLite outbox with idempotency, leases, retries and dead letters."""
import argparse
import json
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox(
  id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL,
  lease_until INTEGER, last_error TEXT);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, accepted_at INTEGER NOT NULL);
"""


def natural(value, name, minimum=0):
    if type(value) is not int or value < minimum: raise ValueError(name + " must be integer >= " + str(minimum))
    return value


class Outbox:
    def __init__(self, database=":memory:", max_attempts=3, base_delay=5):
        self.max_attempts = natural(max_attempts, "max_attempts", 1)
        self.base_delay = natural(base_delay, "base_delay", 1)
        if self.max_attempts > 10: raise ValueError("at most 10 attempts")
        self.connection = sqlite3.connect(database, timeout=0.25)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self): self.connection.close()

    def enqueue(self, key, payload, now=0):
        natural(now, "now")
        if not isinstance(key, str) or not key.strip(): raise ValueError("idempotency key is required")
        if not isinstance(payload, dict): raise ValueError("payload must be an object")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO outbox(id,payload,status,next_attempt) VALUES(?,?,'pending',?)", (key, encoded, now))
            existing = self.connection.execute("SELECT payload FROM outbox WHERE id=?", (key,)).fetchone()
            if existing["payload"] != encoded: raise ValueError("idempotency key reused with different payload")
        return key

    def claim(self, now, limit=10, lease_seconds=10):
        natural(now, "now"); natural(limit, "limit", 1); natural(lease_seconds, "lease_seconds", 1)
        if limit > 1000: raise ValueError("batch limit cannot exceed 1000")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("UPDATE outbox SET status='delivered',lease_until=NULL,last_error=NULL "
                "WHERE status='processing' AND lease_until<=? AND id IN (SELECT id FROM receipts)", (now,))
            self.connection.execute("UPDATE outbox SET status='dead',lease_until=NULL,last_error='lease expired after final attempt' "
                "WHERE status='processing' AND lease_until<=? AND attempts>=?", (now,self.max_attempts))
            rows = self.connection.execute("SELECT * FROM outbox WHERE attempts<? AND ((status='pending' AND next_attempt<=?) "
                "OR (status='processing' AND lease_until<=?)) ORDER BY next_attempt,id LIMIT ?",
                (self.max_attempts, now, now, limit)).fetchall()
            claimed = []
            for row in rows:
                self.connection.execute("UPDATE outbox SET status='processing',attempts=attempts+1,lease_until=? WHERE id=?", (now+lease_seconds,row["id"]))
                claimed.append(dict(self.connection.execute("SELECT * FROM outbox WHERE id=?", (row["id"],)).fetchone()))
            self.connection.commit()
            return claimed
        except Exception:
            self.connection.rollback()
            raise

    def deliver(self, message, now, outcome="success"):
        natural(now, "now")
        if outcome not in {"success", "failure", "accepted_then_crash"}: raise ValueError("unknown simulated transport outcome")
        # Take the write lock before reading the lease so a second worker cannot
        # reclaim it between validation and the receipt/acknowledgment writes.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("SELECT * FROM outbox WHERE id=?", (message["id"],)).fetchone()
            if row is None or row["status"] != "processing" or row["attempts"] != message["attempts"] or row["lease_until"] <= now:
                raise ValueError("stale or expired claim")
            receipt = self.connection.execute("SELECT id FROM receipts WHERE id=?", (row["id"],)).fetchone()
            if receipt or outcome in {"success", "accepted_then_crash"}:
                self.connection.execute("INSERT OR IGNORE INTO receipts VALUES (?,?)", (row["id"],now))
                if outcome == "accepted_then_crash" and not receipt:
                    return "accepted_without_ack"
                self.connection.execute("UPDATE outbox SET status='delivered',lease_until=NULL,last_error=NULL WHERE id=?", (row["id"],))
                return "delivered"
            status = "dead" if row["attempts"] >= self.max_attempts else "pending"
            retry_at = now + self.base_delay * (2 ** (row["attempts"] - 1))
            self.connection.execute("UPDATE outbox SET status=?,next_attempt=?,lease_until=NULL,last_error='synthetic transport failure' WHERE id=?",
                                    (status,retry_at,row["id"]))
            return status
