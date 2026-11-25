from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from station.source.delivery import Delivery, LaneEnd


class ConfirmationState(StrEnum):
    PENDING = 'pending'
    UNKNOWN = 'unknown'
    RELEASED = 'released'


@dataclass(frozen=True)
class CaptureEvidence:
    delivery: Delivery
    confirmation: ConfirmationState


@dataclass(frozen=True)
class CaptureSnapshot:
    roll_id: str
    deliveries: tuple[CaptureEvidence, ...]
    ends: tuple[LaneEnd, ...]

    def last_sequence(self, lane: str) -> int | None:
        for end in self.ends:
            if end.lane == lane:
                return end.last_sequence
        return None


@dataclass(frozen=True)
class SourceReading:
    lane: str
    sequence: int
    value: int
    origin: str


def same_delivery(captured: CaptureEvidence, offered: Delivery) -> bool:
    return captured.delivery == offered


def lane_is_covered(snapshot: CaptureSnapshot, lane: str) -> bool:
    last_sequence = snapshot.last_sequence(lane)
    if last_sequence is None:
        return False
    sequences = sorted(item.delivery.sequence for item in snapshot.deliveries
                       if item.delivery.lane == lane)
    return len(sequences) == last_sequence and all(
        sequence == expected for expected, sequence in enumerate(sequences, 1)
    )


def group_by_position(snapshot: CaptureSnapshot) -> dict[str, tuple[SourceReading, ...]]:
    grouped = defaultdict(list)
    for evidence in snapshot.deliveries:
        delivery = evidence.delivery
        grouped[delivery.position].append(
            SourceReading(delivery.lane, delivery.sequence, delivery.value, delivery.origin)
        )
    return {position: tuple(sorted(readings, key=lambda item: (item.lane, item.sequence)))
            for position, readings in grouped.items()}


def distinct_values(readings: tuple[SourceReading, ...]) -> tuple[int, ...]:
    return tuple(sorted({reading.value for reading in readings}))
