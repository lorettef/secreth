"""pg_dump/pg_restore wrappers: no shell interpolation and no credentials in argv."""

import argparse
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.engine import make_url
from dotenv import load_dotenv


def target(environment_variable):
    load_dotenv()
    value = os.environ.get(environment_variable)
    if not value:
        raise ValueError(f"Set {environment_variable}")
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise ValueError("Target must be an explicit PostgreSQL database")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", url.database):
        raise ValueError(
            "Database name must contain only letters, digits, underscore or dash"
        )
    environment = os.environ.copy()
    for key in ("PGDATABASE", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGSERVICE"):
        environment.pop(key, None)
    environment.update(
        PGDATABASE=url.database,
        PGHOST=url.host or "localhost",
        PGPORT=str(url.port or 5432),
    )
    if url.username:
        environment["PGUSER"] = url.username
    if url.password:
        environment["PGPASSWORD"] = url.password
    for key, val in url.query.items():
        mapping = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT"}
        if key not in mapping or not isinstance(val, str):
            raise ValueError("Unsupported PostgreSQL URL option")
        environment[mapping[key]] = val
    return environment, f"{url.host or 'localhost'}:{url.port or 5432}/{url.database}"


def backup(directory: Path, environment_variable="DATABASE_URL") -> Path:
    environment, label = target(environment_variable)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = (
        directory
        / f"secret_hitler_{datetime.now(timezone.utc):%Y-%m-%d_%H%M%S_%f}.dump"
    )
    created = False
    try:
        descriptor = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-password"],
                env=environment,
                stdout=output,
                stderr=subprocess.PIPE,
                check=True,
            )
    except Exception:
        if created:
            filename.unlink(missing_ok=True)
        raise
    print(f"Backup complete: {label} -> {filename}")
    return filename


def restore(filename: Path, environment_variable: str, *, yes=False):
    environment, label = target(environment_variable)
    if not filename.is_file():
        raise ValueError("Dump file must exist")
    print(f"Restore target: {label} (use a fresh empty database)")
    if not yes and input("Type RESTORE to continue: ") != "RESTORE":
        raise ValueError("Restore cancelled")
    # No DROP/--clean; an occupied database causes rollback, never silent overwrite.
    subprocess.run(
        [
            "pg_restore",
            "--no-password",
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            environment["PGDATABASE"],
            str(filename.resolve()),
        ],
        env=environment,
        stderr=subprocess.PIPE,
        check=True,
    )
    print("Restore complete")


def main(action):
    parser = argparse.ArgumentParser(description=f"PostgreSQL {action}")
    parser.add_argument(
        "--target-env",
        default="DATABASE_URL" if action == "backup" else None,
        required=action == "restore",
        help="Name of environment variable containing explicit target URL",
    )
    if action == "backup":
        parser.add_argument("--directory", type=Path, default=Path("backups"))
    else:
        parser.add_argument("dump", type=Path)
        parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    try:
        if action == "backup":
            backup(args.directory, args.target_env)
        else:
            restore(args.dump, args.target_env, yes=args.yes)
    except Exception as error:
        parser.exit(
            2,
            f"{action} failed ({type(error).__name__}); inspect PostgreSQL connectivity, target and file.\n",
        )
