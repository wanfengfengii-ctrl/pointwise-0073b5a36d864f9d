from dataclasses import dataclass

from station.capture.evidence import CaptureSnapshot, SourceReading, distinct_values, group_by_position
from station.roster.state import RollRoster


@dataclass(frozen=True)
class PositionObservation:
    position: str
    sources: tuple[SourceReading, ...]

    @property
    def values(self) -> tuple[int, ...]:
        return distinct_values(self.sources)


def build_observations(roster: RollRoster, snapshot: CaptureSnapshot) -> tuple[PositionObservation, ...]:
    grouped = group_by_position(snapshot)
    return tuple(PositionObservation(position.label, grouped.get(position.label, ()))
                 for position in roster.positions)
