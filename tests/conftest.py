"""Reject unsafe integration targets before any module's migration fixture runs."""

import os
import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def check_test_database(test_url, production_url=None):
    parsed = make_url(test_url)
    if (
        parsed.drivername != "postgresql+asyncpg"
        or not parsed.database
        or not parsed.database.endswith("_test")
    ):
        raise ValueError(
            "TEST_DATABASE_URL must use postgresql+asyncpg and a database ending in _test"
        )
    if production_url:
        production = make_url(production_url)

        def identity(url):
            host = (
                "localhost"
                if url.host in (None, "localhost", "127.0.0.1", "::1")
                else url.host
            )
            return host, url.port or 5432, url.database

        if identity(parsed) == identity(production):
            raise ValueError(
                "Test database must differ from DATABASE_URL, including .env"
            )


def pytest_sessionstart(session):
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        try:
            check_test_database(test_url, os.environ.get("DATABASE_URL"))
            check_test_database(test_url, dotenv_values(".env").get("DATABASE_URL"))
        except Exception as error:
            raise pytest.UsageError(
                "Unsafe test database configuration; use a separate *_test database"
            ) from error
