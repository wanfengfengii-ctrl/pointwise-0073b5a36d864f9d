import sqlite3
from concurrent.futures import ThreadPoolExecutor

from station.archive.issue import seal
from station.archive.retrieve import get
from station.capture.recovery import recover_capture
from station.roster.register import register
from station.roster.store import transaction
from station.source.replay import initialize_station
from station.source.session import disconnect, offer
from station.capture.intake import accept
from tests import StationCase


class ArchiveTests(StationCase):
    def assert_waiting(self, lanes):
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'waiting')
        self.assertEqual(result.waiting_lanes, lanes)
        self.assertIsNone(result.roll)
        self.assertIsNone(result.conflict)
        self.assertIsNone(get(self.database, self.roll_id))

    def stop_receipt_insert(self):
        with transaction(self.database, write=True) as connection:
            connection.execute('''
                CREATE TRIGGER reject_receipt BEFORE INSERT ON first_receipts
                BEGIN SELECT RAISE(ABORT, 'receipt write stopped'); END
            ''')

    def resume_receipt_insert(self):
        with transaction(self.database, write=True) as connection:
            connection.execute('DROP TRIGGER reject_receipt')

    def test_end_overtakes_delivery(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        self.end('B', 2)
        self.assert_waiting(('B',))
        self.stage('B', 2, '2', 11, 'B2')
        self.assert_waiting(('B',))
        self.stage('B', 1, '1', 7, 'B1')
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        self.assertEqual([row.value for row in result.roll.positions], [7, 11, None])
        self.assertEqual([source.origin for source in result.roll.positions[0].sources], ['A1', 'B1'])
        self.assertEqual(get(self.database, self.roll_id), result.roll)

    def test_one_lane_no_other_evidence(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        self.assert_waiting(('B',))
        self.stage('B', 1, '1', 7, 'B1')
        self.stage('B', 2, '2', 11, 'B2')
        self.assert_waiting(('B',))
        self.end('B', 2)
        self.assertEqual([row.value for row in seal(self.database, self.roll_id).roll.positions],
                         [7, 11, None])

    def test_end_replay(self):
        self.stage('B', 2, '2', 11, 'B2')
        with self.assertRaises(ValueError):
            self.end('B', 1)
        self.assertIsNone(recover_capture(self.database, self.roll_id).last_sequence('B'))
        first = self.end('B', 2)
        self.assertEqual(self.end('B', 2), first)
        with self.assertRaises(ValueError):
            self.end('B', 3)
        with self.assertRaises(ValueError):
            self.stage('B', 3, '3', 12, 'B3')
        self.end('A', 0)
        self.assert_waiting(('B',))
        self.stage('B', 1, '1', 7, 'B1')
        snapshot = recover_capture(self.database, self.roll_id)
        self.assertEqual(snapshot.last_sequence('B'), 2)
        self.assertEqual(len(snapshot.deliveries), 2)
        self.assertEqual(seal(self.database, self.roll_id).status, 'issued')

    def test_equal_mirrors_and_missing(self):
        self.complete_mirror()
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        roll = result.roll
        self.assertEqual([(row.position, row.value) for row in roll.positions],
                         [('1', 7), ('2', 11), ('3', None)])
        self.assertEqual((roll.measured, roll.missing), (2, 1))
        self.assertEqual([[(s.lane, s.sequence, s.origin) for s in row.sources]
                          for row in roll.positions], [[('A', 1, 'A1'), ('B', 1, 'B1')],
                                                       [('B', 2, 'B2')], []])
        self.assertEqual(roll.receipt.receipt_id, 'receipt/R17')
        self.assertEqual(get(self.database, self.roll_id), roll)

    def test_zero_and_order(self):
        roll_id = 'ORDER'
        register(self.database, roll_id, self.shift, ('3', '1', '2'))
        self.stage('A', 2, '3', 0, 'A2', roll_id=roll_id)
        self.stage('A', 1, '1', -(10 ** 25), 'A1', roll_id=roll_id)
        self.end('A', 2, roll_id=roll_id)
        self.end('B', 0, roll_id=roll_id)
        seal(self.database, roll_id)
        roll = get(self.database, roll_id)
        self.assertEqual([(row.position, row.value) for row in roll.positions],
                         [('3', 0), ('1', -(10 ** 25)), ('2', None)])
        self.assertEqual((roll.measured, roll.missing), (2, 1))

    def test_late_mirror_disagrees(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        self.end('B', 2)
        self.stage('B', 2, '2', 11, 'B2')
        self.assert_waiting(('B',))
        self.stage('B', 1, '1', 9, 'B1')
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'conflict')
        self.assertIsNone(result.roll)
        self.assertEqual((result.conflict.position, result.conflict.values), ('1', (7, 9)))
        self.assertEqual([item.origin for item in result.conflict.sources], ['A1', 'B1'])
        originals = recover_capture(self.database, self.roll_id).deliveries
        self.assertEqual([(item.delivery.value, item.delivery.origin) for item in originals],
                         [(7, 'A1'), (9, 'B1'), (11, 'B2')])
        self.assertIsNone(get(self.database, self.roll_id))
        with transaction(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 0)

    def test_publish_failure_can_retry(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        source = self.source('A', item)
        accept(self.database, self.roll_id, offer(source), source)
        self.end('A', 1)
        self.end('B', 0)
        self.stop_receipt_insert()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt write stopped'):
            seal(self.database, self.roll_id)
        disconnect(source)
        initialize_station(self.database)
        self.assertIsNone(get(self.database, self.roll_id))
        self.assertEqual(recover_capture(self.database, self.roll_id).deliveries[0].delivery, item)
        self.resume_receipt_insert()
        first = seal(self.database, self.roll_id)
        self.assertEqual(first.status, 'issued')
        self.assertEqual(first.roll.receipt.receipt_id, 'receipt/R17')
        self.assertEqual(seal(self.database, self.roll_id), first)
        self.assertEqual(get(self.database, self.roll_id), first.roll)
        with transaction(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)

    def test_no_partial_publication(self):
        register(self.database, 'PREVIOUS', self.shift, ('2',))
        self.end('A', 0, roll_id='PREVIOUS')
        self.end('B', 0, roll_id='PREVIOUS')
        previous = seal(self.database, 'PREVIOUS').roll
        self.complete_mirror()
        self.stop_receipt_insert()
        with self.assertRaises(sqlite3.IntegrityError):
            seal(self.database, self.roll_id)
        with transaction(self.database) as connection:
            bodies = connection.execute('SELECT roll_id FROM sealed_rolls').fetchall()
            receipts = connection.execute('SELECT roll_id FROM first_receipts').fetchall()
        self.assertEqual(bodies, [('PREVIOUS',)])
        self.assertEqual(receipts, [('PREVIOUS',)])
        self.assertIsNone(get(self.database, self.roll_id))
        self.assertEqual(get(self.database, 'PREVIOUS'), previous)
        self.resume_receipt_insert()
        self.assertEqual(seal(self.database, self.roll_id).status, 'issued')
        self.assertEqual(get(self.database, 'PREVIOUS'), previous)

    def test_empty_lane_and_history(self):
        self.end('A', 0)
        self.stage('B', 1, '2', 11, 'B1')
        self.end('B', 1)
        first = seal(self.database, self.roll_id).roll
        register(self.database, 'NEXT', '白班', ('2', '1'))
        initialize_station(self.database)
        result = get(self.database, self.roll_id)
        self.assertEqual(result, first)
        self.assertEqual([row.value for row in result.positions], [None, 11, None])
        self.assertEqual(result.positions[1].sources[0].origin, 'B1')
        self.assertEqual(result.receipt.receipt_id, 'receipt/R17')
        self.assertIsNone(get(self.database, 'NEXT'))

    def test_requested_roll_after_other_seal(self):
        self.end('A', 0)
        self.stage('B', 1, '2', 11, 'B1')
        self.end('B', 1)
        first = seal(self.database, self.roll_id).roll
        register(self.database, 'NEXT', '白班', ('2', '1'))
        self.stage('A', 1, '1', 13, 'NEXT-A1', roll_id='NEXT')
        self.end('A', 1, roll_id='NEXT')
        self.end('B', 0, roll_id='NEXT')
        second = seal(self.database, 'NEXT').roll
        initialize_station(self.database)
        self.assertEqual([get(self.database, key) for key in (self.roll_id, 'NEXT', self.roll_id)],
                         [first, second, first])
        self.assertEqual([row.position for row in first.positions], ['1', '2', '3'])
        self.assertEqual([row.position for row in second.positions], ['2', '1'])
        self.assertEqual((first.measured, first.missing, second.measured, second.missing),
                         (1, 2, 1, 1))
        self.assertEqual(second.positions[1].sources[0].origin, 'NEXT-A1')
        self.assertEqual((first.receipt.receipt_id, second.receipt.receipt_id),
                         ('receipt/R17', 'receipt/NEXT'))

    def test_concurrent_seal_reuses_first_receipt(self):
        self.complete_mirror()
        with ThreadPoolExecutor(max_workers=2) as workers:
            calls = [workers.submit(seal, self.database, self.roll_id) for _ in range(2)]
            first, second = [call.result() for call in calls]
        self.assertEqual(first.status, 'issued')
        self.assertEqual(first, second)
        self.assertEqual(get(self.database, self.roll_id), first.roll)
        with transaction(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM first_receipts').fetchone()[0], 1)
