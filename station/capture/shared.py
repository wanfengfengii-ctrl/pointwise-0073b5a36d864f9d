import sqlite3
from dataclasses import dataclass

from examples.shared_frame_device import Frame, Header
from station.capture.evidence import ConfirmationState, SharedSourceReading
from station.source.delivery import DeliveryIdentity, LaneEnd


@dataclass(frozen=True)
class CapturedHeader:
    header: Header
    confirmation: ConfirmationState


@dataclass(frozen=True)
class SharedSnapshot:
    roll_id: str
    headers: tuple[CapturedHeader, ...]
    frames: tuple[Frame, ...]
    ends: tuple[LaneEnd, ...]

    def last_sequence(self, lane: str) -> int | None:
        for end in self.ends:
            if end.lane == lane:
                return end.last_sequence
        return None

    def frame(self, frame_id: str) -> Frame | None:
        for frame in self.frames:
            if frame.frame_id == frame_id:
                return frame
        return None


def initialize_shared(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS shared_headers (
            roll_id TEXT NOT NULL REFERENCES rosters (roll_id),
            lane TEXT NOT NULL CHECK (lane IN ('A', 'B')),
            sequence TEXT NOT NULL,
            position TEXT NOT NULL,
            origin TEXT NOT NULL,
            frame_id TEXT NOT NULL,
            confirmation TEXT NOT NULL CHECK (confirmation IN ('pending', 'unknown', 'released')),
            PRIMARY KEY (roll_id, lane, sequence)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS shared_frames (
            roll_id TEXT NOT NULL REFERENCES rosters (roll_id),
            frame_id TEXT NOT NULL,
            value TEXT NOT NULL,
            raw TEXT NOT NULL,
            PRIMARY KEY (roll_id, frame_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS shared_holds (
            roll_id TEXT NOT NULL REFERENCES rosters (roll_id),
            frame_id TEXT NOT NULL,
            PRIMARY KEY (roll_id, frame_id)
        )
    ''')


def load_hold(connection: sqlite3.Connection, roll_id: str, frame_id: str) -> bool:
    row = connection.execute(
        'SELECT 1 FROM shared_holds WHERE roll_id = ? AND frame_id = ?', (roll_id, frame_id)).fetchone()
    return row is not None


def record_hold(connection: sqlite3.Connection, roll_id: str, frame_id: str) -> None:
    connection.execute('INSERT OR IGNORE INTO shared_holds (roll_id, frame_id) VALUES (?, ?)',
                       (roll_id, frame_id))


def delete_hold(connection: sqlite3.Connection, roll_id: str, frame_id: str) -> None:
    connection.execute('DELETE FROM shared_holds WHERE roll_id = ? AND frame_id = ?',
                       (roll_id, frame_id))


def _identity_key(identity: DeliveryIdentity) -> tuple[str, str, str]:
    roll_id, lane, sequence = identity
    return roll_id, lane, str(sequence)


def _as_header(roll_id: str, row: tuple) -> CapturedHeader:
    lane, sequence, position, origin, frame_id, confirmation = row
    return CapturedHeader(
        Header(roll_id, lane, int(sequence), position, origin, frame_id),
        ConfirmationState(confirmation),
    )


def load_header(connection: sqlite3.Connection, identity: DeliveryIdentity) -> CapturedHeader | None:
    row = connection.execute('''
        SELECT lane, sequence, position, origin, frame_id, confirmation
        FROM shared_headers WHERE roll_id = ? AND lane = ? AND sequence = ?
    ''', _identity_key(identity)).fetchone()
    return None if row is None else _as_header(identity[0], row)


def load_headers(connection: sqlite3.Connection, roll_id: str) -> tuple[CapturedHeader, ...]:
    rows = connection.execute('''
        SELECT lane, sequence, position, origin, frame_id, confirmation
        FROM shared_headers WHERE roll_id = ?
        ORDER BY lane, length(sequence), sequence
    ''', (roll_id,))
    return tuple(_as_header(roll_id, row) for row in rows)


def record_header(connection: sqlite3.Connection, header: Header) -> CapturedHeader:
    connection.execute('''
        INSERT INTO shared_headers
            (roll_id, lane, sequence, position, origin, frame_id, confirmation)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (*_identity_key(header.identity), header.position, header.origin, header.frame_id,
          ConfirmationState.PENDING.value))
    return CapturedHeader(header, ConfirmationState.PENDING)


def write_header_confirmation(connection: sqlite3.Connection, identity: DeliveryIdentity,
                              state: ConfirmationState) -> None:
    connection.execute('''
        UPDATE shared_headers SET confirmation = ?
        WHERE roll_id = ? AND lane = ? AND sequence = ? AND confirmation != 'released'
    ''', (state.value, *_identity_key(identity)))


def _as_frame(roll_id: str, row: tuple) -> Frame:
    frame_id, value, raw = row
    return Frame(roll_id, frame_id, int(value), raw)


def load_frame(connection: sqlite3.Connection, roll_id: str, frame_id: str) -> Frame | None:
    row = connection.execute(
        'SELECT frame_id, value, raw FROM shared_frames WHERE roll_id = ? AND frame_id = ?',
        (roll_id, frame_id)).fetchone()
    return None if row is None else _as_frame(roll_id, row)


def load_frames(connection: sqlite3.Connection, roll_id: str) -> tuple[Frame, ...]:
    rows = connection.execute(
        'SELECT frame_id, value, raw FROM shared_frames WHERE roll_id = ? ORDER BY frame_id',
        (roll_id,))
    return tuple(_as_frame(roll_id, row) for row in rows)


def record_frame(connection: sqlite3.Connection, frame: Frame) -> Frame:
    connection.execute('INSERT INTO shared_frames (roll_id, frame_id, value, raw) VALUES (?, ?, ?, ?)',
                       (frame.roll_id, frame.frame_id, str(frame.value), frame.raw))
    return frame


def read_shared_snapshot(connection: sqlite3.Connection, roll_id: str) -> SharedSnapshot:
    ends = tuple(LaneEnd(roll_id, lane, int(sequence)) for lane, sequence in connection.execute(
        'SELECT lane, last_sequence FROM lane_ends WHERE roll_id = ? ORDER BY lane', (roll_id,)
    ))
    return SharedSnapshot(roll_id, load_headers(connection, roll_id),
                          load_frames(connection, roll_id), ends)


def lane_headers_covered(snapshot: SharedSnapshot, lane: str) -> bool:
    last_sequence = snapshot.last_sequence(lane)
    if last_sequence is None:
        return False
    sequences = sorted(captured.header.sequence for captured in snapshot.headers
                       if captured.header.lane == lane)
    return len(sequences) == last_sequence and all(
        sequence == expected for expected, sequence in enumerate(sequences, 1)
    )


def referenced_frame_ids(snapshot: SharedSnapshot) -> tuple[str, ...]:
    return tuple(sorted({captured.header.frame_id for captured in snapshot.headers}))


def waiting_frame_ids(snapshot: SharedSnapshot) -> tuple[str, ...]:
    captured = {frame.frame_id for frame in snapshot.frames}
    return tuple(frame_id for frame_id in referenced_frame_ids(snapshot) if frame_id not in captured)


def group_shared_by_position(snapshot: SharedSnapshot) -> dict[str, tuple[SharedSourceReading, ...]]:
    grouped: dict[str, list[SharedSourceReading]] = {}
    for captured in snapshot.headers:
        frame = snapshot.frame(captured.header.frame_id)
        if frame is None:
            # The body still owes; the reading is unknown until the frame is captured.
            continue
        header = captured.header
        grouped.setdefault(header.position, []).append(
            SharedSourceReading(header.lane, header.sequence, frame.value, header.origin,
                                header.frame_id))
    return {position: tuple(sorted(readings, key=lambda item: (item.lane, item.sequence)))
            for position, readings in grouped.items()}
