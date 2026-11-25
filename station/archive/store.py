import json
import sqlite3
from dataclasses import asdict

from examples.shared_frame_device import Frame
from station.archive.roll import FirstReceipt, SealedPosition, SealedRoll, SharedSealedRoll
from station.capture.evidence import SourceReading, SharedSourceReading


def initialize_archive(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS sealed_rolls (
            roll_id TEXT PRIMARY KEY NOT NULL REFERENCES rosters (roll_id),
            body TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS first_receipts (
            roll_id TEXT PRIMARY KEY NOT NULL REFERENCES sealed_rolls (roll_id),
            receipt_id TEXT NOT NULL UNIQUE
        )
    ''')


def _as_reading(roll_id: str, reading: dict) -> SourceReading:
    if 'frame_id' in reading:
        return SharedSourceReading(reading['lane'], reading['sequence'], reading['value'],
                                   reading['origin'], reading['frame_id'])
    return SourceReading(reading['lane'], reading['sequence'], reading['value'], reading['origin'])


def load_sealed_roll(connection: sqlite3.Connection, roll_id: str) -> SealedRoll | None:
    row = connection.execute('''
        SELECT sealed_rolls.body, first_receipts.receipt_id
        FROM sealed_rolls JOIN first_receipts USING (roll_id)
        WHERE roll_id = ?
    ''', (roll_id,)).fetchone()
    if row is None:
        return None
    encoded, receipt_id = row
    body = json.loads(encoded)
    positions = tuple(SealedPosition(
        position['position'], position['value'],
        tuple(_as_reading(roll_id, reading) for reading in position['sources']),
    ) for position in body['positions'])
    receipt = FirstReceipt(roll_id, receipt_id)
    if 'frames' in body:
        frames = {frame_id: Frame(roll_id, frame_id, **frame)
                  for frame_id, frame in body['frames'].items()}
        return SharedSealedRoll(roll_id, body['shift'], positions, body['measured'], body['missing'],
                                receipt, frames)
    return SealedRoll(roll_id, body['shift'], positions, body['measured'], body['missing'], receipt)


def publish_roll(connection: sqlite3.Connection, roll: SealedRoll) -> None:
    # Issue owns the transaction for eligibility, construction and both inserts.
    body = {
        'shift': roll.shift,
        'positions': [asdict(position) for position in roll.positions],
        'measured': roll.measured,
        'missing': roll.missing,
    }
    if isinstance(roll, SharedSealedRoll):
        body['frames'] = {frame_id: {'value': frame.value, 'raw': frame.raw}
                          for frame_id, frame in (roll.frames or {}).items()}
    connection.execute('INSERT INTO sealed_rolls (roll_id, body) VALUES (?, ?)',
                       (roll.roll_id, json.dumps(body, ensure_ascii=False)))
    connection.execute('INSERT INTO first_receipts (roll_id, receipt_id) VALUES (?, ?)',
                       (roll.roll_id, roll.receipt.receipt_id))
