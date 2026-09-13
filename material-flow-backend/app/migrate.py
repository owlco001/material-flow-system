"""Explicit deployment entry point for database initialization and migration."""

from app.main import init_db


def main() -> None:
    init_db()


if __name__ == "__main__":
    main()
