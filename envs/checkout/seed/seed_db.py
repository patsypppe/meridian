"""Build the seeded store database at image build time.

Running this during the build rather than at trial start keeps the trial's
startup deterministic and cheap: every trial gets a byte-identical copy of the
same database from the image's read-only layer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

TARGET = Path(__file__).parent / "store.db"

ORDERS = [(order_id, "pending", None) for order_id in range(1041, 1048)]

COUPONS = [
    ("SPRING10", 10, "2026-12-31"),
    ("SUMMER20", 20, "2026-08-13"),  # expired by one day against the tasks' as_of
    ("LAUNCH15", 15, "2026-02-01"),  # expired by months
    ("BOUNDARY7", 7, "2026-08-14"),  # expires exactly on as_of, so it is still valid
    ("WELCOME5", 5, "2027-06-30"),
]


def main() -> None:
    TARGET.unlink(missing_ok=True)
    connection = sqlite3.connect(TARGET)
    connection.executescript(
        """
        CREATE TABLE orders (
            id          INTEGER PRIMARY KEY,
            status      TEXT NOT NULL,
            total_cents INTEGER
        );
        CREATE TABLE coupons (
            code        TEXT PRIMARY KEY,
            percent_off INTEGER NOT NULL,
            expires_on  TEXT NOT NULL
        );
        """
    )
    connection.executemany("INSERT INTO orders VALUES (?, ?, ?)", ORDERS)
    connection.executemany("INSERT INTO coupons VALUES (?, ?, ?)", COUPONS)
    connection.commit()
    connection.close()


if __name__ == "__main__":
    main()
