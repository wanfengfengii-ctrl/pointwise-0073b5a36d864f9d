import sqlite3

from station.roster.state import RollRoster, RosterStatus
from station.roster.store import write_roster_status


def resolve_registration(stored: RollRoster, requested: RollRoster) -> RollRoster:
    if (stored.shift != requested.shift or stored.positions != requested.positions
            or stored.mode is not requested.mode):
        raise ValueError(f'roll {stored.roll_id!r} already has a different roster')
    return stored


def mark_capture_started(connection: sqlite3.Connection, roster: RollRoster) -> None:
    if roster.status is RosterStatus.REGISTERED:
        write_roster_status(connection, roster.roll_id, RosterStatus.CAPTURE_STARTED)
