from station.capture.evidence import CaptureEvidence, ConfirmationState
from station.capture.storage import load_delivery, write_confirmation_state
from station.roster.store import Database, transaction
from station.source.delivery import DeliveryIdentity
from station.source.release import ReleaseOutcome, ReleaseReplyLost, lookup_release, release_delivery
from station.source.session import LaneSession, SourceDisconnected, require_session


def _require_evidence(connection, identity: DeliveryIdentity) -> CaptureEvidence:
    evidence = load_delivery(connection, identity)
    if evidence is None:
        raise KeyError(f'no durable delivery for {identity!r}')
    return evidence


def _apply_outcome(database: Database, identity: DeliveryIdentity,
                   state: ConfirmationState) -> CaptureEvidence:
    with transaction(database, write=True) as connection:
        write_confirmation_state(connection, identity, state)
        return _require_evidence(connection, identity)


def confirm_release(database: Database, identity: DeliveryIdentity,
                    session: LaneSession) -> CaptureEvidence:
    require_session(session, identity[0], identity[1])
    with transaction(database) as connection:
        evidence = _require_evidence(connection, identity)
    if evidence.confirmation is ConfirmationState.RELEASED:
        return evidence
    try:
        outcome = release_delivery(session, identity, expected=evidence.delivery)
    except (ReleaseReplyLost, SourceDisconnected):
        outcome = ReleaseOutcome.UNKNOWN
    if outcome is ReleaseOutcome.WAITING:
        return evidence
    state = (ConfirmationState.RELEASED if outcome is ReleaseOutcome.RELEASED
             else ConfirmationState.UNKNOWN)
    return _apply_outcome(database, identity, state)


def recover_release_status(database: Database, identity: DeliveryIdentity,
                           session: LaneSession) -> CaptureEvidence:
    with transaction(database) as connection:
        evidence = _require_evidence(connection, identity)
    require_session(session, identity[0], identity[1])
    if evidence.confirmation is ConfirmationState.RELEASED:
        return evidence
    try:
        outcome = lookup_release(session, identity)
    except SourceDisconnected:
        outcome = ReleaseOutcome.UNKNOWN
    if outcome is ReleaseOutcome.RELEASED:
        return _apply_outcome(database, identity, ConfirmationState.RELEASED)
    if outcome is ReleaseOutcome.WAITING:
        return confirm_release(database, identity, session)
    return _apply_outcome(database, identity, ConfirmationState.UNKNOWN)
