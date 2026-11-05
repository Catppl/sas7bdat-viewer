from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path


def create_snapshot(
    root: Path,
    domain: str,
    metadata: dict[str, str],
    rows: list[dict[str, object]],
    *,
    snapshot_id: str = "synthetic-snapshot",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source_row_field = "_source_row"
    variables = [
        {
            "name": name,
            "sas_type": sas_type,
            "sqlite_type": "TEXT" if sas_type == "character" else "REAL",
            "nullable": True,
        }
        for name, sas_type in metadata.items()
    ]
    manifest = {
        "snapshot_schema_version": 1,
        "snapshot_id": snapshot_id,
        "created_at": "2026-11-05T00:00:00Z",
        "domain": domain,
        "source_fingerprint": "synthetic-source",
        "source_row_count": len(rows),
        "table_name": "records",
        "source_row_field": source_row_field,
        "variables": variables,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    connection = sqlite3.connect(root / "records.sqlite")
    definitions = [f'"{source_row_field}" INTEGER NOT NULL']
    definitions.extend(f'"{item["name"]}" {item["sqlite_type"]}' for item in variables)
    connection.execute(f"CREATE TABLE records ({', '.join(definitions)})")
    columns = [source_row_field, *metadata]
    placeholders = ", ".join("?" for _ in columns)
    quoted_columns = ", ".join(f'"{item}"' for item in columns)
    connection.executemany(
        f"INSERT INTO records ({quoted_columns}) VALUES ({placeholders})",
        [[row.get(column) for column in columns] for row in rows],
    )
    connection.execute(
        'CREATE UNIQUE INDEX records_source_row_uq ON records ("_source_row")'
    )
    connection.commit()
    connection.close()
    return root


def create_fixture_snapshot(root: Path, domain: str, fixtures: Path) -> Path:
    metadata_by_domain = json.loads(
        (fixtures / "synthetic_current_metadata.json").read_text(encoding="utf-8")
    )
    metadata = metadata_by_domain[domain]
    filename = {
        "AE": "synthetic_current_ae.csv",
        "LB": "synthetic_current_lb.csv",
    }[domain]
    rows: list[dict[str, object]] = []
    with (fixtures / filename).open(encoding="utf-8", newline="") as stream:
        for csv_row in csv.DictReader(stream):
            row: dict[str, object] = {"_source_row": int(csv_row["_source_row"])}
            for name, sas_type in metadata.items():
                raw = csv_row[name]
                if raw == "":
                    row[name] = None
                elif sas_type == "numeric":
                    row[name] = float(raw)
                else:
                    row[name] = raw
            rows.append(row)
    return create_snapshot(root, domain, metadata, rows)
