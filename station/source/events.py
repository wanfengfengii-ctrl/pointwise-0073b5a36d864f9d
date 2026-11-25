from dataclasses import dataclass
from enum import StrEnum

from station.source.delivery import Delivery, LaneEnd, validate_lane


class EventKind(StrEnum):
    DELIVERY = 'delivery'
    END = 'end'
    LOSE_REPLY = 'lose_reply'
    DISCONNECT = 'disconnect'
    RECONNECT = 'reconnect'
    PREVIEW = 'preview'
    SEAL = 'seal'

    @property
    def is_checkpoint(self) -> bool:
        return self in (EventKind.PREVIEW, EventKind.SEAL)


@dataclass(frozen=True)
class RecordedEvent:
    kind: EventKind
    lane: str | None = None
    payload: Delivery | LaneEnd | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind):
            raise ValueError('expected a recorded event kind')
        if self.kind.is_checkpoint:
            if self.lane is not None or self.payload is not None:
                raise ValueError('job checkpoints have no lane or payload')
            return
        validate_lane(self.lane)
        if self.kind is EventKind.DELIVERY:
            if not isinstance(self.payload, Delivery) or self.payload.lane != self.lane:
                raise ValueError('delivery payload must match the event lane')
        elif self.kind is EventKind.END:
            if not isinstance(self.payload, LaneEnd) or self.payload.lane != self.lane:
                raise ValueError('end payload must match the event lane')
        elif self.payload is not None:
            raise ValueError('device control events have no payload')


def decode_event(roll_id: str, data: dict) -> RecordedEvent:
    if not isinstance(data, dict):
        raise ValueError('event must be an object')
    kind = EventKind(data.get('kind'))
    fields = {'kind'}
    if not kind.is_checkpoint:
        fields.add('lane')
    if kind is EventKind.DELIVERY:
        fields.update(('sequence', 'position', 'value', 'origin', 'raw'))
    if kind is EventKind.END:
        fields.add('last_sequence')
    if fields != data.keys():
        raise ValueError(f'invalid fields for {kind.value!r} event')
    lane = data.get('lane')
    if kind is EventKind.DELIVERY:
        payload = Delivery(roll_id, lane, data['sequence'], data['position'], data['value'],
                           data['origin'], data['raw'])
    elif kind is EventKind.END:
        payload = LaneEnd(roll_id, lane, data['last_sequence'])
    else:
        payload = None
    return RecordedEvent(kind, lane, payload)
