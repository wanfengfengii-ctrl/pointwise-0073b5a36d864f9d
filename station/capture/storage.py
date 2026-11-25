import sqlite3

from station.capture.evidence import CaptureEvidence, CaptureSnapshot, ConfirmationState
from station.source.delivery import Delivery, DeliveryIdentity, LaneEnd


def initialize_capture(connection: sqlite3.Connection) -> None:
    # Decimal text preserves instrument integers beyond SQLite's signed integer range.
    connection.execute('''
        CREATE TABLE IF NOT EXISTS captures (
            roll_id TEXT NOT NULL REFERENCES rosters (roll_id),
            lane TEXT NOT NULL CHECK (lane IN ('A', 'B')),
            sequence TEXT NOT NULL,
            position TEXT NOT NULL,
            value TEXT NOT NULL,
            origin TEXT NOT NULL,
            raw TEXT NOT NULL,
            confirmation TEXT NOT NULL CHECK (confirmation IN ('pending', 'unknown', 'released')),
            PRIMARY KEY (roll_id, lane, sequence)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS lane_ends (
            roll_id TEXT NOT NULL REFERENCES rosters (roll_id),
            lane TEXT NOT NULL CHECK (lane IN ('A', 'B')),
            last_sequence TEXT NOT NULL,
            PRIMARY KEY (roll_id, lane)
        )
    ''')


def _as_evidence(roll_id: str, row: tuple) -> CaptureEvidence:
    lane, sequence, position, value, origin, raw, confirmation = row
    delivery = Delivery(roll_id, lane, int(sequence), position, int(value), origin, raw)
    return CaptureEvidence(delivery, ConfirmationState(confirmation))


def _identity_key(identity: DeliveryIdentity) -> tuple[str, str, str]:
    roll_id, lane, sequence = identity
    return roll_id, lane, str(sequence)


def load_delivery(connection: sqlite3.Connection, identity: DeliveryIdentity) -> CaptureEvidence | None:
    row = connection.execute('''
        SELECT lane, sequence, position, value, origin, raw, confirmation
        FROM captures WHERE roll_id = ? AND lane = ? AND sequence = ?
    ''', _identity_key(identity)).fetchone()
    return None if row is None else _as_evidence(identity[0], row)


def record_delivery(connection: sqlite3.Connection, delivery: Delivery) -> CaptureEvidence:
    connection.execute('''
        INSERT INTO captures (roll_id, lane, sequence, position, value, origin, raw, confirmation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (*_identity_key(delivery.identity), delivery.position, str(delivery.value), delivery.origin,
          delivery.raw, ConfirmationState.PENDING.value))
    return CaptureEvidence(delivery, ConfirmationState.PENDING)


def load_lane_end(connection: sqlite3.Connection, roll_id: str, lane: str) -> LaneEnd | None:
    row = connection.execute('SELECT last_sequence FROM lane_ends WHERE roll_id = ? AND lane = ?',
                             (roll_id, lane)).fetchone()
    return None if row is None else LaneEnd(roll_id, lane, int(row[0]))


def record_lane_end(connection: sqlite3.Connection, end: LaneEnd) -> LaneEnd:
    connection.execute('INSERT INTO lane_ends (roll_id, lane, last_sequence) VALUES (?, ?, ?)',
                       (end.roll_id, end.lane, str(end.last_sequence)))
    return end


def read_snapshot(connection: sqlite3.Connection, roll_id: str) -> CaptureSnapshot:
    rows = connection.execute('''
        SELECT lane, sequence, position, value, origin, raw, confirmation
        FROM captures WHERE roll_id = ?
        ORDER BY lane, length(sequence), sequence
    ''', (roll_id,))
    deliveries = tuple(_as_evidence(roll_id, row) for row in rows)
    ends = tuple(LaneEnd(roll_id, lane, int(sequence)) for lane, sequence in connection.execute(
        'SELECT lane, last_sequence FROM lane_ends WHERE roll_id = ? ORDER BY lane', (roll_id,)
    ))
    return CaptureSnapshot(roll_id, deliveries, ends)


def load_pending_confirmations(connection: sqlite3.Connection, roll_id: str,
                               lane: str) -> tuple[DeliveryIdentity, ...]:
    rows = connection.execute('''
        SELECT sequence FROM captures
        WHERE roll_id = ? AND lane = ? AND confirmation != 'released'
        ORDER BY length(sequence), sequence
    ''', (roll_id, lane))
    return tuple((roll_id, lane, int(sequence)) for (sequence,) in rows)


def write_confirmation_state(connection: sqlite3.Connection, identity: DeliveryIdentity,
                             state: ConfirmationState) -> None:
    connection.execute('''
        UPDATE captures SET confirmation = ?
        WHERE roll_id = ? AND lane = ? AND sequence = ? AND confirmation != 'released'
    ''', (state.value, *_identity_key(identity)))
