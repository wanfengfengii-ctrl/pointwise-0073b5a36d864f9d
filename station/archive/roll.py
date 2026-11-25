from dataclasses import dataclass

from examples.shared_frame_device import Frame
from station.capture.evidence import (CaptureSnapshot, SourceReading, distinct_values,
                                      group_by_position)
from station.capture.shared import SharedSnapshot, group_shared_by_position
from station.roster.state import RollRoster


@dataclass(frozen=True)
class SealedPosition:
    position: str
    value: int | None
    sources: tuple[SourceReading, ...]


@dataclass(frozen=True)
class FirstReceipt:
    roll_id: str
    receipt_id: str


@dataclass(frozen=True)
class SealedRoll:
    roll_id: str
    shift: str
    positions: tuple[SealedPosition, ...]
    measured: int
    missing: int
    receipt: FirstReceipt


@dataclass(frozen=True)
class SharedSealedRoll(SealedRoll):
    # Keyed by frame id so a frame shared by both lanes is stored exactly once.
    frames: dict[str, Frame] | None = None


@dataclass(frozen=True)
class MirrorConflict:
    position: str
    values: tuple[int, ...]
    sources: tuple[SourceReading, ...]


def _seal_positions(roster: RollRoster, grouped) -> tuple[SealedPosition, ...] | MirrorConflict:
    positions = []
    for position in roster.positions:
        sources = grouped.get(position.label, ())
        values = distinct_values(sources)
        if len(values) > 1:
            return MirrorConflict(position.label, values, sources)
        positions.append(SealedPosition(position.label, values[0] if values else None, sources))
    return tuple(positions)


def _counts(positions: tuple[SealedPosition, ...]) -> tuple[int, int]:
    measured = sum(1 for position in positions if position.value is not None)
    return measured, len(positions) - measured


def build_roll(roster: RollRoster, snapshot: CaptureSnapshot) -> SealedRoll | MirrorConflict:
    positions = _seal_positions(roster, group_by_position(snapshot))
    if isinstance(positions, MirrorConflict):
        return positions
    measured, missing = _counts(positions)
    receipt = FirstReceipt(roster.roll_id, f'receipt/{roster.roll_id}')
    return SealedRoll(roster.roll_id, roster.shift, positions, measured, missing, receipt)


def build_shared_roll(roster: RollRoster, snapshot: SharedSnapshot) -> SharedSealedRoll | MirrorConflict:
    positions = _seal_positions(roster, group_shared_by_position(snapshot))
    if isinstance(positions, MirrorConflict):
        return positions
    measured, missing = _counts(positions)
    frames = {frame.frame_id: frame for frame in snapshot.frames
              if frame.frame_id in {item.header.frame_id for item in snapshot.headers}}
    receipt = FirstReceipt(roster.roll_id, f'receipt/{roster.roll_id}')
    return SharedSealedRoll(roster.roll_id, roster.shift, positions, measured, missing, receipt,
                            frames)
