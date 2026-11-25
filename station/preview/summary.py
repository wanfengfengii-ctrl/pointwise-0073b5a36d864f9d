from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from station.capture.evidence import CaptureSnapshot, SourceReading, lane_is_covered
from station.capture.shared import SharedSnapshot, lane_headers_covered, waiting_frame_ids
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
    waiting_frames: tuple[str, ...] = ()


def build_summary(snapshot: CaptureSnapshot, observations: tuple[PositionObservation, ...],
                  conflicts: tuple[PreviewConflict, ...]) -> ProgressSummary:
    waiting = tuple(lane for lane in LANES if not lane_is_covered(snapshot, lane))
    return _summarize(snapshot.roll_id, waiting, observations, conflicts, (), frozenset())


def build_shared_summary(snapshot: SharedSnapshot, observations: tuple[PositionObservation, ...],
                         conflicts: tuple[PreviewConflict, ...]) -> ProgressSummary:
    waiting = tuple(lane for lane in LANES if not lane_headers_covered(snapshot, lane))
    captured = {frame.frame_id for frame in snapshot.frames}
    # Positions whose captured headers still owe a body cannot be declared missing yet.
    awaiting_body = frozenset(
        captured_header.header.position for captured_header in snapshot.headers
        if captured_header.header.frame_id not in captured
    )
    return _summarize(snapshot.roll_id, waiting, observations, conflicts,
                      waiting_frame_ids(snapshot), awaiting_body)


def _summarize(roll_id: str, waiting: tuple[str, ...], observations: tuple[PositionObservation, ...],
               conflicts: tuple[PreviewConflict, ...], waiting_frames: tuple[str, ...],
               awaiting_body: frozenset[str]) -> ProgressSummary:
    conflicting = {conflict.position for conflict in conflicts}
    positions = []
    for observation in observations:
        value = None
        if observation.position in conflicting:
            status = PreviewStatus.CONFLICT
        elif observation.sources:
            status = PreviewStatus.OBSERVED
            value = observation.sources[0].value
        elif waiting or observation.position in awaiting_body:
            status = PreviewStatus.PENDING
        else:
            status = PreviewStatus.MISSING
        positions.append(PreviewPosition(observation.position, status, value, observation.sources))
    counts = Counter(position.status for position in positions)
    return ProgressSummary(roll_id, waiting, tuple(positions), conflicts,
                           counts[PreviewStatus.OBSERVED], counts[PreviewStatus.PENDING],
                           counts[PreviewStatus.MISSING], waiting_frames)
