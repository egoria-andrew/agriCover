"""SQLite storage and preview seed data for AgriCover."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from security import hash_password


class Database:
    """Small repository wrapper using parameterized SQLite queries."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("DATABASE_PATH", "instance/agricover.sqlite3")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    phone TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    village TEXT NOT NULL,
                    district TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'farmer' CHECK(role IN ('farmer','agent','admin')),
                    consent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    premium_ugx INTEGER NOT NULL,
                    billing_unit TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    farmer_id INTEGER NOT NULL REFERENCES users(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    insured_units INTEGER NOT NULL DEFAULT 1,
                    premium_ugx INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'awaiting_payment',
                    payment_status TEXT NOT NULL DEFAULT 'not_started',
                    demo_reference TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    farmer_id INTEGER NOT NULL REFERENCES users(id),
                    policy_id INTEGER NOT NULL REFERENCES policies(id),
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_review',
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_read INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS ix_policies_farmer ON policies(farmer_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_claims_farmer ON claims(farmer_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, created_at);
            """)
            products = [
                ("crop", "Weather-indexed crop cover", "Proposed cover for crops such as maize, beans, and sunflower. Trigger and benefits require insurer agreement.", 50000, "policy / season"),
                ("livestock", "Livestock mortality cover", "Proposed cover for livestock losses. Eligibility and benefits require insurer agreement.", 50000, "policy / season"),
            ]
            db.executemany("""INSERT OR IGNORE INTO products
                (code,name,summary,premium_ugx,billing_unit,active) VALUES (?,?,?,?,?,1)""", products)

            preview_users = [
                ("AgriCover Administrator", "0700000001", os.environ.get("ADMIN_PASSWORD", "Admin#12"), "Laroo", "Gulu", "admin"),
                ("Field Agent", "0700000002", os.environ.get("AGENT_PASSWORD", "Agent#12"), "Laroo", "Gulu", "agent"),
                ("Farmer Profile", "0700000003", os.environ.get("FARMER_PASSWORD", "Farm#123"), "Palaro", "Gulu", "farmer"),
            ]
            for name, phone, password, village, district, role in preview_users:
                password_hash = hash_password(password)
                db.execute("""INSERT OR IGNORE INTO users
                    (full_name,phone,password_hash,village,district,role,consent)
                    VALUES (?,?,?,?,?,?,1)""",
                    (name, phone, password_hash, village, district, role))
                # Keep the three seeded walkthrough accounts in sync with their local defaults
                # or Render environment variables, including on an already-created SQLite file.
                db.execute("UPDATE users SET full_name=?,password_hash=?,village=?,district=?,role=?,consent=1 WHERE phone=?",
                           (name, password_hash, village, district, role, phone))
            db.execute("UPDATE users SET full_name='Field Agent' WHERE phone='0700000002' AND full_name='Field Agent Demo'")
            db.execute("UPDATE users SET full_name='Farmer Profile' WHERE phone='0700000003' AND full_name='Farmer Demo'")
            farmer = db.execute("SELECT id FROM users WHERE phone=?", ("0700000003",)).fetchone()
            product = db.execute("SELECT id FROM products WHERE code='crop'").fetchone()
            if farmer and product:
                db.execute("""INSERT INTO notifications(user_id,title,body)
                    SELECT ?, 'Welcome to AgriCover',
                    'This is a learning preview. No real money or insurance cover is involved.'
                    WHERE NOT EXISTS (SELECT 1 FROM notifications WHERE user_id=? AND title='Welcome to AgriCover')""",
                    (farmer["id"], farmer["id"]))
                db.execute("UPDATE notifications SET title='Welcome to AgriCover', body='This is a learning preview. No real money or insurance cover is involved.' WHERE user_id=? AND title='Welcome to the AgriCover demo'", (farmer["id"],))

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.session() as db:
            return db.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.session() as db:
            return db.execute(sql, params).fetchall()

    def run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.session() as db:
            cur = db.execute(sql, params)
            return cur.lastrowid or 0

    def run_many(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        with self.session() as db:
            db.executemany(sql, rows)

