from collections.abc import Iterable

from station.roster.positions import Position, require_text, validate_positions
from station.roster.revisions import resolve_registration
from station.roster.state import RollMode, RollRoster
from station.roster.store import Database, insert_roster, load_roster, transaction


def _register(database: Database, roll_id: str, shift: str, positions: Iterable[str | Position],
              mode: RollMode) -> RollRoster:
    requested = RollRoster(require_text(roll_id, 'roll_id'), require_text(shift, 'shift'),
                           validate_positions(positions), mode=mode)
    with transaction(database, write=True) as connection:
        stored = load_roster(connection, roll_id)
        if stored is not None:
            return resolve_registration(stored, requested)
        insert_roster(connection, requested)
    return requested


def register(database: Database, roll_id: str, shift: str,
             positions: Iterable[str | Position]) -> RollRoster:
    return _register(database, roll_id, shift, positions, RollMode.DELIVERY)


def register_shared(database: Database, roll_id: str, shift: str,
                    positions: Iterable[str | Position]) -> RollRoster:
    return _register(database, roll_id, shift, positions, RollMode.SHARED)
