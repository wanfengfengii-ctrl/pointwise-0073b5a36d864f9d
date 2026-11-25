from dataclasses import dataclass

from station.capture.evidence import CaptureSnapshot, SourceReading, distinct_values, group_by_position
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
class MirrorConflict:
    position: str
    values: tuple[int, ...]
    sources: tuple[SourceReading, ...]


def build_roll(roster: RollRoster, snapshot: CaptureSnapshot) -> SealedRoll | MirrorConflict:
    grouped = group_by_position(snapshot)
    positions = []
    measured = 0
    for position in roster.positions:
        sources = grouped.get(position.label, ())
        values = distinct_values(sources)
        if len(values) > 1:
            return MirrorConflict(position.label, values, sources)
        value = values[0] if values else None
        if value is not None:
            measured += 1
        positions.append(SealedPosition(position.label, value, sources))
    receipt = FirstReceipt(roster.roll_id, f'receipt/{roster.roll_id}')
    return SealedRoll(roster.roll_id, roster.shift, tuple(positions), measured,
                      len(positions) - measured, receipt)
