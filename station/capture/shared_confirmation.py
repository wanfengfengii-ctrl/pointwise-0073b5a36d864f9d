from station.capture.shared import (CapturedHeader, delete_hold, load_frame, load_frames,
                                    load_header, load_headers, load_hold, record_hold,
                                    write_header_confirmation)
from station.capture.evidence import ConfirmationState
from station.roster.store import Database, transaction
from station.source.release import ReleaseReplyLost
from station.source.session import SourceDisconnected


def _frame_durable(database: Database, roll_id: str, frame_id: str) -> bool:
    with transaction(database) as connection:
        return load_frame(connection, roll_id, frame_id) is not None


def _preserve_if_last(database: Database, header, device) -> None:
    """Hold the original frame before its last device reference is returned."""
    roll_id, frame_id = header.roll_id, header.frame_id
    if _frame_durable(database, roll_id, frame_id):
        return
    refs = device.references(roll_id, frame_id)
    if refs - {header.lane}:
        # Another reference keeps the body alive past this return.
        return
    # Establish the physical hold first; only then make it durable.
    device.hold_frame(roll_id, frame_id)
    with transaction(database, write=True) as connection:
        record_hold(connection, roll_id, frame_id)


def _physical_return(database: Database, header, device) -> ConfirmationState:
    if device.returned_header(header.identity) == header:
        return ConfirmationState.RELEASED
    if device.read_header(header.lane) != header:
        # The slot no longer carries this header, so its physical return is done.
        return ConfirmationState.RELEASED
    _preserve_if_last(database, header, device)
    try:
        device.return_slot(header)
    except ReleaseReplyLost:
        return ConfirmationState.UNKNOWN
    return ConfirmationState.RELEASED


def release_header(database: Database, captured: CapturedHeader, device) -> CapturedHeader:
    """Return one captured header's slot, preserving the body when it is the last reference."""
    if captured.confirmation is ConfirmationState.RELEASED:
        return captured
    try:
        with device.serialized:
            state = _physical_return(database, captured.header, device)
    except SourceDisconnected:
        return captured
    with transaction(database, write=True) as connection:
        write_header_confirmation(connection, captured.header.identity, state)
        return load_header(connection, captured.header.identity)


def _pending_headers(database: Database, roll_id: str, frame_id: str) -> tuple[CapturedHeader, ...]:
    with transaction(database) as connection:
        return tuple(captured for captured in load_headers(connection, roll_id)
                     if captured.header.frame_id == frame_id
                     and captured.confirmation is not ConfirmationState.RELEASED)


def _drop_hold(database: Database, roll_id: str, frame_id: str, device) -> bool:
    """Release the station's hardware hold once the frame body is durable."""
    with transaction(database) as connection:
        recorded = load_hold(connection, roll_id, frame_id)
    try:
        with device.serialized:
            device.drop_hold(roll_id, frame_id)
    except SourceDisconnected:
        return False
    if recorded:
        with transaction(database, write=True) as connection:
            delete_hold(connection, roll_id, frame_id)
    return True


def settle_frame(database: Database, roll_id: str, frame_id: str, device) -> None:
    """Free station holds and header slots now that the frame body is durable."""
    _drop_hold(database, roll_id, frame_id, device)
    for captured in _pending_headers(database, roll_id, frame_id):
        release_header(database, captured, device)


def recover_shared_releases(database: Database, roll_id: str, device) -> None:
    """Retry outstanding returns and release holds whose bodies are already durable."""
    with transaction(database) as connection:
        frame_ids = tuple(frame.frame_id for frame in load_frames(connection, roll_id))
        pending = tuple(captured for captured in load_headers(connection, roll_id)
                        if captured.confirmation is not ConfirmationState.RELEASED)
    for frame_id in frame_ids:
        settle_frame(database, roll_id, frame_id, device)
    for captured in pending:
        release_header(database, captured, device)
