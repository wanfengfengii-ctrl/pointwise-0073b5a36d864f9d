import json
import sqlite3
from dataclasses import asdict

from station.archive.roll import FirstReceipt, SealedPosition, SealedRoll
from station.capture.evidence import SourceReading


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
        tuple(SourceReading(**reading) for reading in position['sources']),
    ) for position in body['positions'])
    return SealedRoll(roll_id, body['shift'], positions, body['measured'], body['missing'],
                      FirstReceipt(roll_id, receipt_id))


def publish_roll(connection: sqlite3.Connection, roll: SealedRoll) -> None:
    # Issue owns the transaction for eligibility, construction and both inserts.
    body = json.dumps({
        'shift': roll.shift,
        'positions': [asdict(position) for position in roll.positions],
        'measured': roll.measured,
        'missing': roll.missing,
    }, ensure_ascii=False)
    connection.execute('INSERT INTO sealed_rolls (roll_id, body) VALUES (?, ?)',
                       (roll.roll_id, body))
    connection.execute('INSERT INTO first_receipts (roll_id, receipt_id) VALUES (?, ?)',
                       (roll.roll_id, roll.receipt.receipt_id))
