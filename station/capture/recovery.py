from examples.shared_frame_device import Frame
from station.capture.confirmation import recover_release_status
from station.capture.evidence import CaptureEvidence, CaptureSnapshot
from station.capture.shared import (CapturedHeader, SharedSnapshot, load_frame, load_header,
                                    read_shared_snapshot)
from station.capture.shared_confirmation import recover_shared_releases
from station.capture.storage import load_delivery, load_pending_confirmations, read_snapshot
from station.roster.positions import require_text
from station.roster.state import RollMode
from station.roster.store import Database, require_roster, transaction
from station.source.delivery import DeliveryIdentity, validate_lane, validate_sequence
from station.source.session import LaneSession, require_session


def find_accept_result(database: Database, identity: DeliveryIdentity) -> CaptureEvidence | None:
    validate_lane(identity[1])
    validate_sequence(identity[2], minimum=1)
    with transaction(database) as connection:
        return load_delivery(connection, identity)


def find_header(database: Database, identity: DeliveryIdentity) -> CapturedHeader | None:
    validate_lane(identity[1])
    validate_sequence(identity[2], minimum=1)
    with transaction(database) as connection:
        return load_header(connection, identity)


def find_frame(database: Database, roll_id: str, frame_id: str) -> Frame | None:
    require_text(roll_id, 'roll_id')
    require_text(frame_id, 'frame_id')
    with transaction(database) as connection:
        return load_frame(connection, roll_id, frame_id)


def recover_capture(database: Database, roll_id: str,
                    session: LaneSession | None = None) -> CaptureSnapshot:
    if session is not None:
        require_session(session, roll_id, session.lane)
        with transaction(database) as connection:
            require_roster(connection, roll_id)
            pending = load_pending_confirmations(connection, roll_id, session.lane)
        for identity in pending:
            recover_release_status(database, identity, session)
    with transaction(database) as connection:
        require_roster(connection, roll_id)
        return read_snapshot(connection, roll_id)


def recover_shared(database: Database, roll_id: str, device) -> SharedSnapshot:
    with transaction(database) as connection:
        roster = require_roster(connection, roll_id)
    if roster.mode is not RollMode.SHARED:
        raise ValueError(f'roll {roll_id!r} is not registered for shared frames')
    recover_shared_releases(database, roll_id, device)
    with transaction(database) as connection:
        require_roster(connection, roll_id)
        return read_shared_snapshot(connection, roll_id)
