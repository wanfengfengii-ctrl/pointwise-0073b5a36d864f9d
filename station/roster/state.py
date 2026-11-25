from dataclasses import dataclass
from enum import StrEnum

from station.roster.positions import Position


class RosterStatus(StrEnum):
    REGISTERED = 'registered'
    CAPTURE_STARTED = 'capture_started'


@dataclass(frozen=True)
class RollRoster:
    roll_id: str
    shift: str
    positions: tuple[Position, ...]
    status: RosterStatus = RosterStatus.REGISTERED

    def contains_position(self, label: str) -> bool:
        return any(position.label == label for position in self.positions)
