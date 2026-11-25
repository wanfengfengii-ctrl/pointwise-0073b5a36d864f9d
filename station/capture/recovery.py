from station.capture.confirmation import recover_release_status
from station.capture.evidence import CaptureEvidence, CaptureSnapshot
from station.capture.storage import load_delivery, load_pending_confirmations, read_snapshot
from station.roster.store import Database, require_roster, transaction
from station.source.delivery import DeliveryIdentity, validate_lane, validate_sequence
from station.source.session import LaneSession, require_session


def find_accept_result(database: Database, identity: DeliveryIdentity) -> CaptureEvidence | None:
    validate_lane(identity[1])
    validate_sequence(identity[2], minimum=1)
    with transaction(database) as connection:
        return load_delivery(connection, identity)


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
