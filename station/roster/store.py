import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from os import PathLike

from station.roster.positions import Position
from station.roster.state import RollRoster, RosterStatus

Database = str | PathLike[str]


@contextmanager
def transaction(database: Database, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        with connection:
            yield connection


def initialize_roster(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS rosters (
            roll_id TEXT PRIMARY KEY NOT NULL,
            shift TEXT NOT NULL,
            positions TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('registered', 'capture_started'))
        )
    ''')


def load_roster(connection: sqlite3.Connection, roll_id: str) -> RollRoster | None:
    row = connection.execute(
        'SELECT shift, positions, status FROM rosters WHERE roll_id = ?', (roll_id,)
    ).fetchone()
    if row is None:
        return None
    shift, positions, status = row
    return RollRoster(roll_id, shift, tuple(Position(label) for label in json.loads(positions)),
                      RosterStatus(status))


def require_roster(connection: sqlite3.Connection, roll_id: str) -> RollRoster:
    roster = load_roster(connection, roll_id)
    if roster is None:
        raise KeyError(f'unknown roll {roll_id!r}')
    return roster


def insert_roster(connection: sqlite3.Connection, roster: RollRoster) -> None:
    labels = [position.label for position in roster.positions]
    connection.execute(
        'INSERT INTO rosters (roll_id, shift, positions, status) VALUES (?, ?, ?, ?)',
        (roster.roll_id, roster.shift, json.dumps(labels, ensure_ascii=False), roster.status.value),
    )


def write_roster_status(connection: sqlite3.Connection, roll_id: str, status: RosterStatus) -> None:
    connection.execute('UPDATE rosters SET status = ? WHERE roll_id = ?', (status.value, roll_id))
