from collections import deque
from dataclasses import dataclass, field

from station.source.delivery import Delivery, LaneEnd, validate_lane, validate_sequence
from station.source.events import EventKind
from station.source.recording import Recording


class SourceDisconnected(ConnectionError):
    pass


class DeliveryReleased(LookupError):
    pass


@dataclass
class LaneSession:
    roll_id: str
    lane: str
    steps: deque[tuple[EventKind, int | None]] = field(repr=False)
    originals: dict[int, Delivery] = field(repr=False)
    offered: set[int] = field(default_factory=set, repr=False)
    released: set[int] = field(default_factory=set, repr=False)
    declared_end: LaneEnd | None = None
    connected: bool = True
    lose_next_reply: bool = False


def open_session(recording: Recording, lane: str) -> LaneSession:
    validate_lane(lane)
    steps = deque()
    originals = {}
    for event in recording.lane_events(lane):
        payload = event.payload
        if isinstance(payload, Delivery):
            originals[payload.sequence] = payload
            number = payload.sequence
        elif isinstance(payload, LaneEnd):
            number = payload.last_sequence
        else:
            number = None
        steps.append((event.kind, number))
    return LaneSession(recording.roll_id, lane, steps, originals)


def require_connected(session: LaneSession) -> None:
    if not session.connected:
        raise SourceDisconnected(f'roll {session.roll_id!r} lane {session.lane} is disconnected')


def require_session(session: LaneSession, roll_id: str, lane: str) -> None:
    if (session.roll_id, session.lane) != (roll_id, lane):
        raise ValueError('event does not belong to this device session')


def disconnect(session: LaneSession) -> None:
    session.connected = False


def reconnect(session: LaneSession) -> None:
    session.connected = True


def read_delivery(session: LaneSession, sequence: int) -> Delivery:
    require_connected(session)
    validate_sequence(sequence, minimum=1)
    if sequence in session.released:
        raise DeliveryReleased((session.roll_id, session.lane, sequence))
    if sequence not in session.offered:
        raise LookupError(f'lane {session.lane} has not offered sequence {sequence}')
    return session.originals[sequence]


def offer(session: LaneSession) -> Delivery | LaneEnd | None:
    if session.steps and session.steps[0][0] is EventKind.RECONNECT:
        session.steps.popleft()
        reconnect(session)
        return None
    require_connected(session)
    if not session.steps:
        return None
    kind, number = session.steps.popleft()
    if kind is EventKind.DELIVERY:
        if number not in session.released:
            session.offered.add(number)
        return read_delivery(session, number)
    if kind is EventKind.END:
        session.declared_end = LaneEnd(session.roll_id, session.lane, number)
        return session.declared_end
    if kind is EventKind.LOSE_REPLY:
        session.lose_next_reply = True
    elif kind is EventKind.DISCONNECT:
        disconnect(session)
    return None
