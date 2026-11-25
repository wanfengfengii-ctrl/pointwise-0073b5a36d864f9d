from station.archive.roll import SealedRoll
from station.archive.store import load_sealed_roll
from station.roster.store import Database, transaction


def get(database: Database, roll_id: str) -> SealedRoll | None:
    with transaction(database) as connection:
        return load_sealed_roll(connection, roll_id)
