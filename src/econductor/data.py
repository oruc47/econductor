from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from econductor.config import atomic_json


def connection(work: Path, memory: str = "2GB"):
    import duckdb

    return duckdb.connect(
        config={
            "memory_limit": memory,
            "temp_directory": str(work / "spill"),
            "threads": 4,
            "enable_external_access": True,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
    )


def sql_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def dataset_relation(path: Path, cache: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "," if suffix == ".csv" else "\t"
        return (
            f"read_csv({sql_literal(path)}, header=true, delim='{delimiter}', sample_size=20480)",
            {"source": str(path)},
        )
    if suffix == ".parquet":
        return f"read_parquet({sql_literal(path)})", {"source": str(path)}
    if suffix not in {".dta", ".xlsx"}:
        raise ValueError("Supported datasets: CSV, TSV, Parquet, Stata .dta, Excel .xlsx")
    stat = path.stat()
    key = hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
    target = cache / key
    manifest = target / "metadata.json"
    if not manifest.exists():
        import shutil

        import pyarrow as pa
        import pyarrow.parquet as pq

        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        rows = 0
        metadata = {
            "source": str(path),
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
        }
        if suffix == ".dta":
            import pyreadstat

            _, meta = pyreadstat.read_dta(str(path), metadataonly=True)
            metadata.update(
                {
                    "column_labels": dict(zip(meta.column_names, meta.column_labels)),
                    "value_labels": meta.variable_value_labels,
                    "missing_user_values": meta.missing_user_values,
                    "missing_policy": "Stata numeric missing and extended missing values are SQL nulls; source file remains unchanged.",
                }
            )
            chunks = (
                frame
                for frame, _ in pyreadstat.read_file_in_chunks(
                    pyreadstat.read_dta,
                    str(path),
                    chunksize=50000,
                    apply_value_formats=False,
                    user_missing=False,
                )
            )
        else:
            chunks = excel_chunks(path)
            metadata["sheet"] = "first worksheet"
        schema = None
        for index, frame in enumerate(chunks):
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if schema is None:
                schema = table.schema
            else:
                try:
                    table = table.cast(schema)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
                    raise ValueError(
                        "Column types change across Excel chunks. Normalize the workbook or export CSV before importing."
                    ) from error
            pq.write_table(table, target / f"part-{index:05}.parquet")
            rows += len(frame)
        if not rows:
            raise ValueError("Dataset is empty; no rows to import.")
        metadata["rows"] = rows
        atomic_json(manifest, metadata)
    return f"read_parquet({sql_literal(target / '*.parquet')})", json.loads(manifest.read_text())


def excel_chunks(path: Path):
    import openpyxl
    import pandas as pd

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        iterator = book.worksheets[0].iter_rows(values_only=True)
        raw = next(iterator, None)
        if raw is None:
            raise ValueError("Empty workbook")
        columns = [
            str(value) if value is not None else f"column_{i + 1}" for i, value in enumerate(raw)
        ]
        if len(set(columns)) != len(columns):
            raise ValueError("Excel headers must be unique.")
        batch = []
        for row in iterator:
            batch.append(row)
            if len(batch) == 50000:
                yield pd.DataFrame(batch, columns=columns)
                batch = []
        if batch:
            yield pd.DataFrame(batch, columns=columns)
    finally:
        book.close()


def clean_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value[:1000] if isinstance(value, str) else value
    return str(value)[:1000]


def inspect_dataset(path: Path, cache: Path, work: Path, memory: str) -> dict:
    relation, metadata = dataset_relation(path, cache)
    with connection(work, memory) as con:
        schema = con.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        sample = con.execute(f"SELECT * FROM {relation} LIMIT 8").fetchall()
    return {
        "metadata": metadata,
        "schema": [{"name": r[0], "type": r[1]} for r in schema],
        "sample": [[clean_value(v) for v in r] for r in sample],
        "note": "Sample contains at most 8 rows; row count is not scanned unless requested with SQL.",
    }


def query_dataset(paths: dict[str, Path], query: str, cache: Path, work: Path, memory: str) -> dict:
    import re

    import duckdb

    if not paths:
        raise ValueError("Supply tables mapping aliases to dataset paths.")
    if len(paths) > 10:
        raise ValueError("At most 10 input tables per query.")
    with connection(work, memory) as con:
        for alias, path in paths.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", alias):
                raise ValueError("Table aliases must be simple SQL identifiers.")
            relation, _ = dataset_relation(path, cache)
            con.execute(f'CREATE VIEW "{alias}" AS SELECT * FROM {relation}')
        statements = con.extract_statements(query)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("Only a single SELECT/WITH query is allowed.")
        # Full result goes directly to disk; only a bounded preview enters context.
        output = work / "query.parquet"
        con.execute(
            f"COPY ({query.rstrip().rstrip(';')}) TO {sql_literal(output)} (FORMAT PARQUET)"
        )
        rows = con.execute(f"SELECT * FROM read_parquet({sql_literal(output)}) LIMIT 50").fetchall()
        columns = [entry[0] for entry in con.description]
        count = con.execute(f"SELECT count(*) FROM read_parquet({sql_literal(output)})").fetchone()[
            0
        ]
    return {
        "columns": columns,
        "rows": [[clean_value(v) for v in r] for r in rows],
        "row_count": count,
        "artifact": str(output),
        "note": "Preview limited to 50 rows; full result saved as Parquet.",
    }
