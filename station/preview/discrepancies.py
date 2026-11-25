from dataclasses import dataclass

from station.capture.evidence import SourceReading
from station.preview.observations import PositionObservation


@dataclass(frozen=True)
class PreviewConflict:
    position: str
    values: tuple[int, ...]
    sources: tuple[SourceReading, ...]


def list_conflicts(observations: tuple[PositionObservation, ...]) -> tuple[PreviewConflict, ...]:
    conflicts = []
    for observation in observations:
        values = observation.values
        if len(values) > 1:
            conflicts.append(PreviewConflict(observation.position, values, observation.sources))
    return tuple(conflicts)
