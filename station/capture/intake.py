from station.capture.confirmation import confirm_release
from station.capture.evidence import CaptureEvidence, same_delivery
from station.capture.storage import (load_delivery, load_lane_end, read_snapshot,
                                     record_delivery, record_lane_end)
from station.roster.revisions import mark_capture_started
from station.roster.store import Database, require_roster, transaction
from station.source.delivery import Delivery, LaneEnd
from station.source.session import LaneSession, read_delivery, require_session


def _check_roll(roll_id: str, event: Delivery | LaneEnd, session: LaneSession | None) -> None:
    if roll_id != event.roll_id:
        raise ValueError('event roll does not match the requested roll')
    if session is not None:
        require_session(session, event.roll_id, event.lane)


def accept(database: Database, roll_id: str, event: Delivery | LaneEnd,
           session: LaneSession | None = None) -> CaptureEvidence | LaneEnd:
    if isinstance(event, Delivery):
        return accept_delivery(database, roll_id, event, session)
    if isinstance(event, LaneEnd):
        return accept_lane_end(database, roll_id, event, session)
    raise ValueError('expected a delivery or a lane end')


def accept_delivery(database: Database, roll_id: str, delivery: Delivery,
                    session: LaneSession | None = None) -> CaptureEvidence:
    _check_roll(roll_id, delivery, session)
    with transaction(database, write=True) as connection:
        roster = require_roster(connection, roll_id)
        if not roster.contains_position(delivery.position):
            raise ValueError(f'position {delivery.position!r} is not on roll {roll_id!r}')
        evidence = load_delivery(connection, delivery.identity)
        if evidence is None:
            end = load_lane_end(connection, roll_id, delivery.lane)
            if end is not None and delivery.sequence > end.last_sequence:
                raise ValueError('delivery exceeds the declared lane end')
            if session is not None and read_delivery(session, delivery.sequence) != delivery:
                raise ValueError('delivery differs from the offered device original')
            evidence = record_delivery(connection, delivery)
            mark_capture_started(connection, roster)
        elif not same_delivery(evidence, delivery):
            raise ValueError(f'delivery identity reused with changed payload: {delivery.identity!r}')
    if session is not None:
        return confirm_release(database, delivery.identity, session)
    return evidence


def accept_lane_end(database: Database, roll_id: str, end: LaneEnd,
                    session: LaneSession | None = None) -> LaneEnd:
    _check_roll(roll_id, end, session)
    with transaction(database, write=True) as connection:
        roster = require_roster(connection, roll_id)
        previous = load_lane_end(connection, roll_id, end.lane)
        if previous is not None:
            if previous != end:
                raise ValueError('lane already has a different end declaration')
            return previous
        snapshot = read_snapshot(connection, roll_id)
        if any(item.delivery.lane == end.lane and item.delivery.sequence > end.last_sequence
               for item in snapshot.deliveries):
            raise ValueError('lane end precedes an already captured delivery')
        record_lane_end(connection, end)
        mark_capture_started(connection, roster)
    return end
