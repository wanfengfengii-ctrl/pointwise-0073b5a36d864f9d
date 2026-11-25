import json
from dataclasses import dataclass
from pathlib import Path

from station.roster.positions import require_text, validate_positions
from station.source.delivery import Delivery, DeliveryIdentity
from station.source.events import RecordedEvent, decode_event


@dataclass(frozen=True)
class Recording:
    roll_id: str
    shift: str
    positions: tuple[str, ...]
    events: tuple[RecordedEvent, ...]

    def __post_init__(self) -> None:
        validate_recording(self)

    def lane_events(self, lane: str) -> tuple[RecordedEvent, ...]:
        return tuple(event for event in self.events if event.lane == lane)


def validate_recording(recording: Recording) -> None:
    require_text(recording.roll_id, 'roll_id')
    require_text(recording.shift, 'shift')
    if not isinstance(recording.positions, tuple) or any(
        not isinstance(label, str) for label in recording.positions
    ):
        raise ValueError('recording positions must be a tuple of labels')
    validate_positions(recording.positions)
    if not isinstance(recording.events, tuple):
        raise ValueError('recording events must be a tuple')
    originals: dict[DeliveryIdentity, Delivery] = {}
    for event in recording.events:
        if not isinstance(event, RecordedEvent):
            raise ValueError('expected a recorded event')
        payload = event.payload
        if payload is None:
            continue
        if payload.roll_id != recording.roll_id:
            raise ValueError('recording event belongs to another roll')
        if isinstance(payload, Delivery):
            previous = originals.setdefault(payload.identity, payload)
            if previous != payload:
                raise ValueError(f'recording changes original {payload.identity!r}')


def load_recording(path: str | Path) -> Recording:
    with Path(path).open(encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.keys() != {'roll_id', 'shift', 'positions', 'events'}:
        raise ValueError('recording needs roll_id, shift, positions and events')
    if not isinstance(data['positions'], list) or not isinstance(data['events'], list):
        raise ValueError('positions and events must be arrays')
    roll_id = require_text(data['roll_id'], 'roll_id')
    events = []
    for index, event in enumerate(data['events'], 1):
        try:
            events.append(decode_event(roll_id, event))
        except ValueError as exc:
            raise ValueError(f'event {index}: {exc}') from exc
    return Recording(roll_id, data['shift'], tuple(data['positions']), tuple(events))
