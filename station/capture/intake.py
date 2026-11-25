from examples.shared_frame_device import Frame, Header
from station.capture.confirmation import confirm_release
from station.capture.evidence import CaptureEvidence, same_delivery
from station.capture.shared import (CapturedHeader, load_frame, load_header, read_shared_snapshot,
                                    record_frame, record_header)
from station.capture.shared_confirmation import release_header, settle_frame
from station.capture.storage import (load_delivery, load_lane_end, read_snapshot,
                                     record_delivery, record_lane_end)
from station.roster.revisions import mark_capture_started
from station.roster.state import RollMode
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
    if isinstance(event, (Header, Frame)):
        raise ValueError('shared frame pieces use accept_header or accept_frame')
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
        if roster.mode is RollMode.SHARED:
            raise ValueError(f'shared-frame roll {roll_id!r} does not accept ordinary deliveries')
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
        if roster.mode is RollMode.SHARED:
            snapshot = read_shared_snapshot(connection, roll_id)
            overrun = any(captured.header.lane == end.lane
                          and captured.header.sequence > end.last_sequence
                          for captured in snapshot.headers)
        else:
            snapshot = read_snapshot(connection, roll_id)
            overrun = any(item.delivery.lane == end.lane
                          and item.delivery.sequence > end.last_sequence
                          for item in snapshot.deliveries)
        if overrun:
            raise ValueError('lane end precedes an already captured item')
        record_lane_end(connection, end)
        mark_capture_started(connection, roster)
    return end


def _load_header_for(database: Database, header: Header) -> CapturedHeader | None:
    """Validate the roll mode and, if already captured, return the durable header."""
    with transaction(database) as connection:
        roster = require_roster(connection, header.roll_id)
        if roster.mode is not RollMode.SHARED:
            raise ValueError(f'roll {header.roll_id!r} is not registered for shared frames')
        if not roster.contains_position(header.position):
            raise ValueError(f'position {header.position!r} is not on roll {header.roll_id!r}')
        captured = load_header(connection, header.identity)
        if captured is not None:
            return captured
        end = load_lane_end(connection, header.roll_id, header.lane)
        if end is not None and header.sequence > end.last_sequence:
            raise ValueError('header exceeds the declared lane end')
    return None


def _verify_header_original(header: Header, device) -> None:
    """Compare a first-time header against the device's original of the same identity."""
    returned = device.returned_header(header.identity)
    if returned is not None:
        if returned != header:
            raise ValueError(f'header differs from the returned device original: {header.identity!r}')
        return
    if device.read_header(header.lane) != header:
        raise ValueError(f'header differs from the device original: {header.identity!r}')


def _store_header(database: Database, header: Header) -> CapturedHeader:
    with transaction(database, write=True) as connection:
        roster = require_roster(connection, header.roll_id)
        captured = load_header(connection, header.identity)
        if captured is not None:
            if captured.header != header:
                raise ValueError(
                    f'header identity reused with changed payload: {header.identity!r}')
            return captured
        captured = record_header(connection, header)
        mark_capture_started(connection, roster)
        return captured


def accept_header(database: Database, header: Header, device) -> CapturedHeader:
    if not isinstance(header, Header):
        raise ValueError('expected a shared frame header')
    captured = _load_header_for(database, header)
    if captured is not None:
        if captured.header != header:
            raise ValueError(f'header identity reused with changed payload: {header.identity!r}')
    else:
        _verify_header_original(header, device)
        captured = _store_header(database, header)
    # The header is durable now; only then may its physical slot be freed.
    return release_header(database, captured, device)


def accept_frame(database: Database, frame: Frame, device) -> Frame:
    if not isinstance(frame, Frame):
        raise ValueError('expected a shared frame body')
    with transaction(database) as connection:
        roster = require_roster(connection, frame.roll_id)
        if roster.mode is not RollMode.SHARED:
            raise ValueError(f'roll {frame.roll_id!r} is not registered for shared frames')
        stored = load_frame(connection, frame.roll_id, frame.frame_id)
    if stored is not None:
        if stored != frame:
            raise ValueError(f'frame {frame.frame_id!r} reused with changed payload')
    else:
        if device.read_frame(frame.roll_id, frame.frame_id) != frame:
            raise ValueError(f'frame differs from the device original: {frame.frame_id!r}')
        with transaction(database, write=True) as connection:
            roster = require_roster(connection, frame.roll_id)
            stored = load_frame(connection, frame.roll_id, frame.frame_id)
            if stored is None:
                stored = record_frame(connection, frame)
                mark_capture_started(connection, roster)
            elif stored != frame:
                raise ValueError(f'frame {frame.frame_id!r} reused with changed payload')
    # The body is durable now; settle any station hold and free remaining header slots.
    settle_frame(database, frame.roll_id, frame.frame_id, device)
    return stored
