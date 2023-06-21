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
