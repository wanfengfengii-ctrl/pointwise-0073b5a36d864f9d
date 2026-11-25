from enum import StrEnum

from station.source.delivery import Delivery, DeliveryIdentity, validate_sequence
from station.source.session import LaneSession, require_connected, require_session


class ReleaseOutcome(StrEnum):
    WAITING = 'waiting'
    RELEASED = 'released'
    UNKNOWN = 'unknown'


class ReleaseReplyLost(TimeoutError):
    pass


def lookup_release(session: LaneSession, identity: DeliveryIdentity) -> ReleaseOutcome:
    roll_id, lane, sequence = identity
    require_session(session, roll_id, lane)
    validate_sequence(sequence, minimum=1)
    require_connected(session)
    if sequence in session.released:
        return ReleaseOutcome.RELEASED
    if sequence in session.originals:
        return ReleaseOutcome.WAITING
    return ReleaseOutcome.UNKNOWN


def release_delivery(session: LaneSession, identity: DeliveryIdentity, *,
                     expected: Delivery | None = None) -> ReleaseOutcome:
    outcome = lookup_release(session, identity)
    sequence = identity[2]
    if outcome is not ReleaseOutcome.WAITING or sequence not in session.offered:
        return outcome
    if expected is not None and session.originals[sequence] != expected:
        raise ValueError('device original differs from the captured delivery')
    del session.originals[sequence]
    session.offered.remove(sequence)
    session.released.add(sequence)
    if session.lose_next_reply:
        session.lose_next_reply = False
        raise ReleaseReplyLost(f'device released {identity!r} before the reply was lost')
    return ReleaseOutcome.RELEASED
