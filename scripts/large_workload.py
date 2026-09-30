"""Explicit 20 GiB disk-spill workload; only creates synthetic data in a chosen temp folder."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import duckdb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-gb", type=int, default=20)
    parser.add_argument("--directory", type=Path, help="Dedicated disposable directory")
    args = parser.parse_args()
    if not args.directory:
        parser.error(
            "Pass --directory to a dedicated disposable location; no files are created by default."
        )
    root = args.directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    needed = args.size_gb * 1024**3 + 2 * 1024**3
    if args.size_gb < 1 or shutil.disk_usage(root).free < needed:
        parser.error(f"Need size >= 1 and at least {needed / 1024**3:.1f} GiB free.")
    source, spill, output = root / "synthetic.parquet", root / "spill", root / "aggregate.parquet"
    try:
        with duckdb.connect(
            config={"memory_limit": "2GB", "temp_directory": str(spill), "threads": 4}
        ) as db:
            # Repeating strings and IDs compress heavily. Deterministic payload noise
            # creates a realistic wide value column while keeping the generator bounded.
            db.execute(
                """CREATE TABLE synthetic AS
                   SELECT i::BIGINT id, i % 1000 group_id, hash(i)::UBIGINT score
                   FROM range(0, ?) AS t(i)""",
                [args.size_gb * 40_000_000],
            )
            db.execute(
                "COPY synthetic TO ? (FORMAT PARQUET, COMPRESSION UNCOMPRESSED)", [str(source)]
            )
            db.execute("DROP TABLE synthetic")
            size = source.stat().st_size / 1024**3
            print(f"Generated {size:.2f} GiB at {source}; aggregating under a 2 GiB cap.")
            db.execute(
                """COPY (
                    SELECT group_id, count(*) n, avg(score) mean_score
                    FROM read_parquet(?) GROUP BY group_id
                ) TO ? (FORMAT PARQUET)""",
                [str(source), str(output)],
            )
            rows = db.execute("SELECT count(*) FROM read_parquet(?)", [str(output)]).fetchone()[0]
            print(f"Finished; {rows} group aggregates at {output}.")
    finally:
        print(f"Synthetic files are in {root}. Remove that directory manually after inspection.")


if __name__ == "__main__":
    main()
