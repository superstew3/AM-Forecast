#!/usr/bin/env python3
"""Compile the model metadata to PostgreSQL DDL.

The models are the single source of truth. This script emits the DDL that the
initial migration executes, so the migration can never drift from the models.

Usage:
    python scripts/generate_ddl.py [out.sql]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.models import Base  # noqa: E402

DIALECT = postgresql.dialect()

HEADER = """-- Account Manager Income Forecasting Platform
-- Initial schema. Generated from app/models by scripts/generate_ddl.py.
-- Do not hand-edit: change the models and regenerate.
--
-- Money is numeric(14,2) throughout. No floats in the financial path.
-- All reported income is GST inclusive.
"""


def build() -> str:
    parts = [HEADER]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=DIALECT)).strip()
        parts.append(f"{ddl};\n")
        for index in table.indexes:
            parts.append(str(CreateIndex(index).compile(dialect=DIALECT)).strip() + ";\n")
    return "\n".join(parts)


def main() -> int:
    sql = build()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(sql)
        print(f"wrote {len(sql.splitlines())} lines to {sys.argv[1]}")
    else:
        print(sql)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
