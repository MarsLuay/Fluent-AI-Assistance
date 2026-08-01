"""Run fluentcoder with a project-local catalog index for one subprocess."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fluentcoder against a project-local catalog DB.")
    parser.add_argument("--catalog-db", required=True, type=Path)
    parser.add_argument("fluentcoder_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    fluentcoder_args = list(args.fluentcoder_args)
    if fluentcoder_args and fluentcoder_args[0] == "--":
        fluentcoder_args = fluentcoder_args[1:]
    if not fluentcoder_args:
        parser.error("missing fluentcoder command after --")

    catalog_db = args.catalog_db.expanduser().resolve()
    if not catalog_db.exists():
        parser.error(f"catalog DB does not exist: {catalog_db}")

    os.environ["FLUENTCODER_INDEX_DB"] = str(catalog_db)

    import fluentcoder.catalog as catalog_package
    import fluentcoder.catalog.catalog as catalog_module
    import fluentcoder.catalog.paths as catalog_paths
    from fluentcoder import cli as fluentcoder_cli

    catalog_module.DEFAULT_INDEX_PATH = catalog_db
    catalog_package.DEFAULT_INDEX_PATH = catalog_db
    catalog_paths.DEFAULT_INDEX_PATH = catalog_db
    return fluentcoder_cli.main(fluentcoder_args)


if __name__ == "__main__":
    raise SystemExit(main())
