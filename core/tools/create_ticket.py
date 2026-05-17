"""
create_ticket.py — Save low-confidence queries to local SQLite.
SQLite file: data/tickets.db
Table: tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, timestamp TEXT,
               status TEXT DEFAULT 'open', assigned_to TEXT DEFAULT 'helpdesk')
Expose: save_ticket(query: str) -> int  (returns ticket_id)
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Database path
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "tickets.db"


def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the DB and table if needed."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            assigned_to TEXT DEFAULT 'helpdesk'
        )
    """)
    conn.commit()
    return conn


def save_ticket(query: str) -> int:
    """
    Save a query as a new ticket. Returns the ticket_id.
    """
    conn = _get_connection()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "INSERT INTO tickets (query, timestamp) VALUES (?, ?)",
            (query, ts),
        )
        conn.commit()
        ticket_id = cursor.lastrowid
        print(f"[TICKET] Created ticket #{ticket_id}: \"{query}\"")
        return ticket_id
    finally:
        conn.close()


def list_tickets() -> list[dict]:
    """Return all tickets as a list of dicts."""
    conn = _get_connection()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM tickets ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    print("=== create_ticket.py standalone test ===")
    tid = save_ticket("Test query: không in được phiếu thu")
    print(f"Inserted ticket_id: {tid}")
    all_tickets = list_tickets()
    print(f"All tickets ({len(all_tickets)}):")
    for t in all_tickets:
        print(f"  #{t['id']} | {t['status']} | {t['query']}")
    print("\n✓ create_ticket.py works correctly.")
