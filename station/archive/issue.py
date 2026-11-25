from dataclasses import dataclass
from enum import StrEnum

from station.archive.eligibility import assess_eligibility
from station.archive.roll import MirrorConflict, SealedRoll, build_roll
from station.archive.store import load_sealed_roll, publish_roll
from station.capture.storage import read_snapshot
from station.roster.store import Database, require_roster, transaction


class IssueStatus(StrEnum):
    WAITING = 'waiting'
    CONFLICT = 'conflict'
    ISSUED = 'issued'


@dataclass(frozen=True)
class IssueResult:
    status: IssueStatus
    roll: SealedRoll | None = None
    waiting_lanes: tuple[str, ...] = ()
    conflict: MirrorConflict | None = None


def seal(database: Database, roll_id: str) -> IssueResult:
    with transaction(database, write=True) as connection:
        roster = require_roster(connection, roll_id)
        previous = load_sealed_roll(connection, roll_id)
        if previous is not None:
            return IssueResult(IssueStatus.ISSUED, roll=previous)
        snapshot = read_snapshot(connection, roll_id)
        eligibility = assess_eligibility(snapshot)
        if not eligibility.ready:
            return IssueResult(IssueStatus.WAITING, waiting_lanes=eligibility.waiting_lanes)
        roll = build_roll(roster, snapshot)
        if isinstance(roll, MirrorConflict):
            return IssueResult(IssueStatus.CONFLICT, conflict=roll)
        publish_roll(connection, roll)
    return IssueResult(IssueStatus.ISSUED, roll=roll)
