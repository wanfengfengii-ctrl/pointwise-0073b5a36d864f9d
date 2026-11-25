from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from station.capture.evidence import CaptureSnapshot, SourceReading, lane_is_covered
from station.preview.discrepancies import PreviewConflict
from station.preview.observations import PositionObservation
from station.source.delivery import LANES


class PreviewStatus(StrEnum):
    OBSERVED = 'observed'
    PENDING = 'pending'
    MISSING = 'missing'
    CONFLICT = 'conflict'


@dataclass(frozen=True)
class PreviewPosition:
    position: str
    status: PreviewStatus
    value: int | None
    sources: tuple[SourceReading, ...]


@dataclass(frozen=True)
class ProgressSummary:
    roll_id: str
    waiting_lanes: tuple[str, ...]
    positions: tuple[PreviewPosition, ...]
    conflicts: tuple[PreviewConflict, ...]
    observed: int
    pending: int
    missing: int


def build_summary(snapshot: CaptureSnapshot, observations: tuple[PositionObservation, ...],
                  conflicts: tuple[PreviewConflict, ...]) -> ProgressSummary:
    waiting = tuple(lane for lane in LANES if not lane_is_covered(snapshot, lane))
    conflicting = {conflict.position for conflict in conflicts}
    positions = []
    for observation in observations:
        value = None
        if observation.position in conflicting:
            status = PreviewStatus.CONFLICT
        elif observation.sources:
            status = PreviewStatus.OBSERVED
            value = observation.sources[0].value
        else:
            status = PreviewStatus.PENDING if waiting else PreviewStatus.MISSING
        positions.append(PreviewPosition(observation.position, status, value, observation.sources))
    counts = Counter(position.status for position in positions)
    return ProgressSummary(snapshot.roll_id, waiting, tuple(positions), conflicts,
                           counts[PreviewStatus.OBSERVED], counts[PreviewStatus.PENDING],
                           counts[PreviewStatus.MISSING])
