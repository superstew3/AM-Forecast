#!/usr/bin/env python3
"""Import command line.

    python scripts/import_cli.py <dsn> detect  <file>
    python scripts/import_cli.py <dsn> prepare <file> [--user U]
    python scripts/import_cli.py <dsn> accept  <batch_id> [--user U] [--force]
    python scripts/import_cli.py <dsn> reject  <batch_id> <reason> [--user U]
    python scripts/import_cli.py <dsn> rollback <batch_id> <reason> [--user U] [--force]
    python scripts/import_cli.py <dsn> batches
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importers import accept, detect, prepare, reject, rollback  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dsn, command = sys.argv[1], sys.argv[2]
    args = [a for a in sys.argv[3:] if not a.startswith("--")]
    user = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--user=")), "cli")
    force = "--force" in sys.argv

    if command == "detect":
        d = detect(args[0])
        print(f"{d.label or 'UNKNOWN'}  confidence {d.confidence:.0%}  rows {d.row_count:,}")
        print(f"  scores: {d.scores}")
        print(f"  columns: {len(d.columns)}")
        if d.missing_required:
            print(f"  MISSING REQUIRED: {', '.join(d.missing_required)}")
        for m in d.messages:
            print(f"  ! {m}")
        return 0

    with psycopg2.connect(dsn) as conn:
        if command == "prepare":
            print(prepare(conn, args[0], user).render())
        elif command == "accept":
            print(accept(conn, int(args[0]), user, force=force))
        elif command == "reject":
            print(reject(conn, int(args[0]), args[1], user))
        elif command == "rollback":
            print(rollback(conn, int(args[0]), args[1], user, force=force))
        elif command == "batches":
            with conn.cursor() as cur:
                cur.execute("""SELECT id, file_name, file_type, status, source_row_count,
                                      accepted_row_count, net_income, expected_forecast_income
                               FROM upload_batch ORDER BY id""")
                for r in cur.fetchall():
                    print(f"  {r[0]:>3}  {r[3]:<11} {r[2]:<16} {str(r[1])[:44]:<44} "
                          f"src {r[4] or 0:>6,}  ok {r[5] or 0:>6,}")
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
