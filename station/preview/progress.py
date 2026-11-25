from station.capture.storage import read_snapshot
from station.preview.discrepancies import list_conflicts
from station.preview.observations import build_observations
from station.preview.summary import ProgressSummary, build_summary
from station.roster.store import Database, require_roster, transaction


def progress(database: Database, roll_id: str) -> ProgressSummary:
    with transaction(database) as connection:
        roster = require_roster(connection, roll_id)
        snapshot = read_snapshot(connection, roll_id)
    observations = build_observations(roster, snapshot)
    return build_summary(snapshot, observations, list_conflicts(observations))
