import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from examples.shared_frame_device import Frame, FrameDevice, Header
from station.archive.issue import seal
from station.archive.retrieve import get
from station.capture.confirmation import ConfirmationState
from station.capture.intake import accept, accept_frame, accept_header
from station.capture.recovery import find_accept_result, find_frame, find_header, recover_shared
from station.preview.progress import progress
from station.roster.register import register, register_shared
from station.roster.state import RollMode, RosterStatus
from station.roster.store import load_roster, transaction
from station.source.delivery import Delivery, LaneEnd
from station.source.replay import initialize_station
from station.source.session import SourceDisconnected
from tests.test_frame_device import reject_next_write_commit

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class SharedCase(unittest.TestCase):
    roll_id = 'R28'
    shift = '夜班'
    positions = ('1', '2')

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'shared.sqlite'
        initialize_station(self.database)
        self.roster = register_shared(self.database, self.roll_id, self.shift, self.positions)
        self.scene = json.loads((EXAMPLES / 'shared-frame.json').read_text(encoding='utf-8'))
        self.device = FrameDevice()
        self.frame, (self.a, self.b) = self.stage('shared')

    def stage(self, name):
        group = self.scene[name]
        roll_id = self.scene['roll_id']
        frame = Frame(roll_id, **group['frame'])
        headers = tuple(Header(roll_id, frame_id=frame.frame_id, **item)
                        for item in group['headers'])
        self.device.stage(frame, *headers)
        return frame, headers

    def end(self, lane, last_sequence):
        return accept(self.database, self.roll_id, LaneEnd(self.roll_id, lane, last_sequence))

    def take(self, header):
        return accept_header(self.database, header, self.device)

    def body(self, frame):
        return accept_frame(self.database, frame, self.device)

    def slots(self):
        return {lane: self.device.read_header(lane) for lane in ('A', 'B')}

    def stop_writes(self, table):
        with transaction(self.database, write=True) as connection:
            connection.execute(f'''
                CREATE TRIGGER reject_{table} BEFORE INSERT ON {table}
                BEGIN SELECT RAISE(ABORT, 'write stopped'); END
            ''')

    def resume_writes(self, table):
        with transaction(self.database, write=True) as connection:
            connection.execute(f'DROP TRIGGER reject_{table}')


class SharedRegistrationTests(SharedCase):
    def test_mode_is_fixed_at_registration(self):
        self.assertEqual(self.roster.mode, RollMode.SHARED)
        self.assertEqual(self.roster.status, RosterStatus.REGISTERED)
        same = register_shared(self.database, self.roll_id, self.shift, self.positions)
        self.assertEqual(same, self.roster)
        initialize_station(self.database)
        with transaction(self.database) as connection:
            self.assertEqual(load_roster(connection, self.roll_id).mode, RollMode.SHARED)
        with self.assertRaises(ValueError):
            register(self.database, self.roll_id, self.shift, self.positions)
        with self.assertRaises(ValueError):
            register_shared(self.database, self.roll_id, '白班', self.positions)
        with self.assertRaises(ValueError):
            register_shared(self.database, self.roll_id, self.shift, ('1',))

    def test_ordinary_roll_stays_ordinary(self):
        register(self.database, 'R17', self.shift, ('1',))
        with self.assertRaises(ValueError):
            register_shared(self.database, 'R17', self.shift, ('1',))
        with transaction(self.database) as connection:
            self.assertEqual(load_roster(connection, 'R17').mode, RollMode.DELIVERY)

    def test_shared_roll_rejects_ordinary_delivery(self):
        delivery = Delivery(self.roll_id, 'A', 1, '1', 7, 'A1', '1,7,A1')
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, delivery)

    def test_ordinary_roll_rejects_shared_pieces(self):
        register(self.database, 'R17', self.shift, ('1',))
        header = Header('R17', 'A', 1, '1', 'A1', 'F')
        frame = Frame('R17', 'F', 7, 'f7')
        with self.assertRaises(ValueError):
            accept(self.database, 'R17', header)
        with self.assertRaises(ValueError):
            accept(self.database, 'R17', frame)
        with self.assertRaises(ValueError):
            accept_header(self.database, header, self.device)
        with self.assertRaises(ValueError):
            accept_frame(self.database, frame, self.device)

    def test_unknown_roll_and_position(self):
        header = Header('OTHER', 'A', 1, '1', 'A1', 'F')
        with self.assertRaises(KeyError):
            accept_header(self.database, header, self.device)
        wrong = Header(self.roll_id, 'A', 1, '9', 'A9', 'F')
        with self.assertRaises(ValueError):
            accept_header(self.database, wrong, self.device)


class SharedIntakeTests(SharedCase):
    def test_header_is_verified_and_slot_freed(self):
        captured = self.take(self.a)
        self.assertEqual(captured.header, self.a)
        self.assertEqual(captured.confirmation, ConfirmationState.RELEASED)
        self.assertIsNone(self.slots()['A'])
        self.assertEqual(self.slots()['B'], self.b)
        self.assertEqual(find_header(self.database, self.a.identity), captured)

    def test_changed_header_payload_rejected(self):
        self.take(self.a)
        cases = (
            Header(self.roll_id, 'A', 1, '1', 'A1', 'G'),
            Header(self.roll_id, 'A', 1, '2', 'A1', 'F'),
            Header(self.roll_id, 'A', 1, '1', 'A1b', 'F'),
        )
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    accept_header(self.database, changed, self.device)
        self.assertEqual(find_header(self.database, self.a.identity).header, self.a)

    def test_first_header_differs_from_device_original(self):
        impostor = Header(self.roll_id, 'A', 1, '1', 'A9', 'F')
        with self.assertRaises(ValueError):
            accept_header(self.database, impostor, self.device)
        self.assertIsNone(find_header(self.database, self.a.identity))
        self.assertEqual(self.slots()['A'], self.a)

    def test_frame_is_verified_and_deduped(self):
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        stored = self.body(self.frame)
        self.assertEqual(stored, self.frame)
        self.assertEqual(find_frame(self.database, self.roll_id, 'F'), self.frame)
        self.assertEqual(self.body(self.frame), stored)
        with self.assertRaises(ValueError):
            accept_frame(self.database, Frame(self.roll_id, 'F', 8, 'f7'), self.device)
        with self.assertRaises(ValueError):
            accept_frame(self.database, Frame(self.roll_id, 'F', 7, 'other'), self.device)

    def test_redelivery_reuses_captured_header(self):
        first = self.take(self.a)
        again = self.take(self.a)
        self.assertEqual(again, first)
        self.assertEqual(len(self.slots()), 2)
        with transaction(self.database) as connection:
            count = connection.execute('SELECT count(*) FROM shared_headers').fetchone()[0]
        self.assertEqual(count, 1)

    def test_header_beyond_declared_end(self):
        self.end('A', 0)
        with self.assertRaises(ValueError):
            self.take(self.a)
        self.assertIsNone(find_header(self.database, self.a.identity))
        self.assertEqual(self.slots()['A'], self.a)

    def test_end_precedes_captured_header(self):
        self.take(self.a)
        with self.assertRaises(ValueError):
            self.end('A', 0)
        self.assertIsNone(seal(self.database, self.roll_id).roll)


class SharedSlotTests(SharedCase):
    def test_first_header_frees_without_waiting_other_lane(self):
        self.take(self.a)
        self.assertIsNone(self.slots()['A'])
        self.assertEqual(self.slots()['B'], self.b)
        # B still holds the frame, so no station hold was needed.
        self.assertEqual(self.device.references(*self.frame.key), {'B'})

    def test_body_recovers_after_channel_restored_and_hold_cleared(self):
        self.device.set_body_online(False)
        self.take(self.a)
        self.take(self.b)
        with self.assertRaises(SourceDisconnected):
            self.device.read_frame(*self.frame.key)
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.device.set_body_online(True)
        self.assertEqual(self.device.read_frame(*self.frame.key), self.frame)
        self.body(self.frame)
        self.assertEqual(self.device.references(*self.frame.key), frozenset())
        with self.assertRaises(KeyError):
            self.device.read_frame(*self.frame.key)

    def test_body_before_last_return_needs_no_hold(self):
        self.take(self.a)
        self.device.set_body_online(True)
        self.body(self.frame)
        self.assertEqual(self.slots()['B'], self.b)
        self.take(self.b)
        self.assertIsNone(self.slots()['B'])
        with self.assertRaises(KeyError):
            self.device.read_frame(*self.frame.key)

    def test_first_freed_lane_continues_with_next_frame(self):
        self.device.set_body_online(False)
        self.end('A', 2)
        self.end('B', 1)
        self.take(self.a)
        other, (next_header,) = self.stage('next_a')
        self.take(next_header)
        self.assertEqual(self.device.references(*other.key), {'hold'})
        self.device.set_body_online(True)
        self.body(other)
        self.assertEqual(self.device.references(*other.key), frozenset())
        self.take(self.b)
        # F is still owed even though G shares its value and raw text.
        waiting = seal(self.database, self.roll_id)
        self.assertEqual(waiting.waiting_lanes, ())
        self.assertEqual(waiting.waiting_frames, ('F',))
        self.body(self.frame)
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        self.assertEqual(set(result.roll.frames), {'F', 'G'})

    def test_alternate_return_order_is_symmetric(self):
        self.take(self.b)
        self.assertIsNone(self.slots()['B'])
        self.assertEqual(self.slots()['A'], self.a)
        self.take(self.a)
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})


class SharedSealTests(SharedCase):
    def complete_first_branch(self):
        self.end('A', 1)
        self.end('B', 1)
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        self.body(self.frame)

    def test_waiting_lists_lanes_and_owed_frames(self):
        self.take(self.a)
        self.end('A', 1)
        waiting = seal(self.database, self.roll_id)
        self.assertEqual(waiting.status, 'waiting')
        self.assertEqual(waiting.waiting_lanes, ('B',))
        self.assertEqual(waiting.waiting_frames, ('F',))
        self.assertIsNone(waiting.roll)
        self.assertIsNone(get(self.database, self.roll_id))
        self.end('B', 0)
        waiting = seal(self.database, self.roll_id)
        self.assertEqual(waiting.waiting_lanes, ())
        self.assertEqual(waiting.waiting_frames, ('F',))

    def test_issued_roll_shape(self):
        self.complete_first_branch()
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        roll = result.roll
        self.assertEqual([(row.position, row.value) for row in roll.positions],
                         [('1', 7), ('2', None)])
        self.assertEqual((roll.measured, roll.missing), (1, 1))
        self.assertEqual([(source.lane, source.sequence, source.value, source.origin,
                           source.frame_id) for source in roll.positions[0].sources],
                         [('A', 1, 7, 'A1', 'F'), ('B', 1, 7, 'B1', 'F')])
        self.assertEqual(set(roll.frames), {'F'})
        self.assertEqual((roll.frames['F'].value, roll.frames['F'].raw), (7, 'f7'))
        self.assertEqual(roll.receipt.receipt_id, 'receipt/R28')
        self.assertEqual(get(self.database, self.roll_id), roll)

    def test_two_frames_same_text_stay_distinct(self):
        self.device.set_body_online(False)
        self.end('A', 2)
        self.end('B', 1)
        self.take(self.a)
        other, (next_header,) = self.stage('next_a')
        self.take(next_header)
        self.device.set_body_online(True)
        self.body(other)
        self.take(self.b)
        self.body(self.frame)
        roll = seal(self.database, self.roll_id).roll
        frames = [(source.lane, source.sequence, source.frame_id)
                  for source in roll.positions[0].sources]
        self.assertEqual(frames, [('A', 1, 'F'), ('A', 2, 'G'), ('B', 1, 'F')])
        self.assertEqual(len(roll.frames), 2)
        self.assertNotEqual(roll.frames['F'].frame_id, roll.frames['G'].frame_id)

    def test_conflict_blocks_seal(self):
        self.device.set_body_online(False)
        self.end('A', 2)
        self.end('B', 1)
        self.take(self.a)
        other, (next_header,) = self.stage('next_a')
        self.take(next_header)
        self.take(self.b)
        self.device.set_body_online(True)
        self.body(other)
        self.body(self.frame)
        # Make G disagree with F at the same position.
        with transaction(self.database, write=True) as connection:
            connection.execute("UPDATE shared_frames SET value = '9' WHERE frame_id = 'G'")
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'conflict')
        self.assertEqual(result.conflict.position, '1')
        self.assertEqual(result.conflict.values, (7, 9))
        self.assertIsNone(get(self.database, self.roll_id))

    def test_empty_lane_and_order(self):
        self.device.set_body_online(False)
        self.end('A', 0)
        self.end('B', 1)
        self.take(self.b)
        self.device.set_body_online(True)
        self.body(self.frame)
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        self.assertEqual([(row.position, row.value) for row in result.roll.positions],
                         [('1', 7), ('2', None)])

    def test_preview_waits_for_owed_body(self):
        self.end('A', 1)
        self.end('B', 1)
        self.take(self.a)
        self.take(self.b)
        view = progress(self.database, self.roll_id)
        self.assertEqual(view.waiting_lanes, ())
        self.assertEqual(view.waiting_frames, ('F',))
        self.assertEqual([row.status for row in view.positions], ['pending', 'missing'])
        self.device.set_body_online(True)
        self.body(self.frame)
        view = progress(self.database, self.roll_id)
        self.assertEqual([row.status for row in view.positions], ['observed', 'missing'])
        self.assertEqual(view.positions[0].value, 7)
        self.assertEqual([source.origin for source in view.positions[0].sources], ['A1', 'B1'])


class SharedFailureTests(SharedCase):
    def test_header_commit_failure_keeps_slot_and_retries(self):
        self.end('A', 1)
        self.end('B', 1)
        with reject_next_write_commit():
            with self.assertRaises(sqlite3.DatabaseError):
                self.take(self.a)
        self.assertEqual(self.slots()['A'], self.a)
        self.assertIsNone(find_header(self.database, self.a.identity))
        self.assertEqual(self.take(self.a).confirmation, ConfirmationState.RELEASED)
        self.assertIsNone(self.slots()['A'])

    def test_frame_commit_failure_keeps_last_hold(self):
        self.end('A', 1)
        self.end('B', 1)
        self.take(self.a)
        self.take(self.b)
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.device.set_body_online(True)
        with reject_next_write_commit():
            with self.assertRaises(sqlite3.DatabaseError):
                self.body(self.frame)
        self.assertIsNone(find_frame(self.database, self.roll_id, 'F'))
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.assertEqual(self.device.read_frame(*self.frame.key), self.frame)
        self.assertEqual(self.body(self.frame), self.frame)
        self.assertEqual(self.device.references(*self.frame.key), frozenset())

    def test_frame_write_trigger_failure_keeps_hold(self):
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        self.stop_writes('shared_frames')
        with self.assertRaises(sqlite3.IntegrityError):
            self.body(self.frame)
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.assertIsNone(find_frame(self.database, self.roll_id, 'F'))
        self.resume_writes('shared_frames')
        self.body(self.frame)
        self.assertEqual(self.device.references(*self.frame.key), frozenset())

    def test_seal_write_failure_leaves_no_partial_roll(self):
        self.end('A', 1)
        self.end('B', 1)
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        self.body(self.frame)
        self.stop_writes('first_receipts')
        with self.assertRaises(sqlite3.IntegrityError):
            seal(self.database, self.roll_id)
        self.assertIsNone(get(self.database, self.roll_id))
        self.assertEqual(find_frame(self.database, self.roll_id, 'F'), self.frame)
        self.resume_writes('first_receipts')
        first = seal(self.database, self.roll_id)
        self.assertEqual(first.status, 'issued')
        self.assertEqual(seal(self.database, self.roll_id), first)
        self.assertEqual(get(self.database, self.roll_id), first.roll)

    def test_lost_return_reply_and_redelivery_after_slot_reuse(self):
        self.device.lose_next_return_reply()
        unknown = self.take(self.a)
        self.assertEqual(unknown.confirmation, ConfirmationState.UNKNOWN)
        self.assertIsNone(self.slots()['A'])
        other, (next_header,) = self.stage('next_a')
        self.take(next_header)
        self.assertIsNone(self.slots()['A'])
        # The old header can be settled by identity without disturbing the new slot.
        self.assertEqual(find_header(self.database, self.a.identity).confirmation,
                         ConfirmationState.UNKNOWN)
        settled = self.take(self.a)
        self.assertEqual(settled.confirmation, ConfirmationState.RELEASED)
        self.assertIsNone(self.slots()['A'])
        self.assertEqual(self.slots()['B'], self.b)
        with self.assertRaises(ValueError):
            self.take(Header(self.roll_id, 'A', 1, '1', 'A9', 'F'))
        self.assertIsNone(self.slots()['A'])

    def test_recover_shared_settles_lost_reply_and_hold(self):
        self.device.lose_next_return_reply()
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        snapshot = recover_shared(self.database, self.roll_id, self.device)
        self.assertEqual([captured.confirmation for captured in snapshot.headers],
                         [ConfirmationState.RELEASED, ConfirmationState.RELEASED])
        # The body is still owed, so the station hold remains intact after recovery.
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.body(self.frame)
        self.assertEqual(recover_shared(self.database, self.roll_id, self.device).frames,
                         (self.frame,))

    def test_lost_reply_survives_station_reopen(self):
        self.device.lose_next_return_reply()
        unknown = self.take(self.a)
        self.assertEqual(unknown.confirmation, ConfirmationState.UNKNOWN)
        self.take(self.b)
        # Rebuild every table and reopen with the original device session.
        initialize_station(self.database)
        self.assertEqual(find_header(self.database, self.a.identity), unknown)
        snapshot = recover_shared(self.database, self.roll_id, self.device)
        self.assertEqual([captured.confirmation for captured in snapshot.headers],
                         [ConfirmationState.RELEASED, ConfirmationState.RELEASED])
        self.assertEqual(self.device.references(*self.frame.key), {'hold'})
        self.device.set_body_online(True)
        self.body(self.frame)
        self.assertEqual(seal(self.database, self.roll_id).status, 'waiting')
        self.end('A', 1)
        self.end('B', 1)
        self.assertEqual(seal(self.database, self.roll_id).status, 'issued')

    def test_body_channel_recovers_after_reconnect(self):
        self.device.set_body_online(False)
        self.take(self.a)
        self.take(self.b)
        with self.assertRaises(SourceDisconnected):
            self.device.read_frame(*self.frame.key)
        # The station still resolves the durable headers while the body port is down.
        snapshot = recover_shared(self.database, self.roll_id, self.device)
        self.assertEqual(len(snapshot.headers), 2)
        self.assertEqual(snapshot.frames, ())
        self.device.set_body_online(True)
        recovered = recover_shared(self.database, self.roll_id, self.device)
        self.assertEqual(recovered.frames, ())
        self.body(self.frame)
        self.assertEqual(find_frame(self.database, self.roll_id, 'F'), self.frame)

    def test_ordinary_roll_has_no_shared_recovery(self):
        register(self.database, 'R17', self.shift, ('1',))
        with self.assertRaises(ValueError):
            recover_shared(self.database, 'R17', self.device)

    def test_stopped_source_queries_and_reopen(self):
        self.end('A', 1)
        self.end('B', 1)
        self.take(self.a)
        self.take(self.b)
        self.device.set_body_online(True)
        self.body(self.frame)
        issued = seal(self.database, self.roll_id).roll
        self.device.set_online(False)
        header = find_header(self.database, self.a.identity)
        self.assertEqual((header.header.origin, header.header.frame_id,
                          header.confirmation), ('A1', 'F', ConfirmationState.RELEASED))
        self.assertEqual(find_frame(self.database, self.roll_id, 'F'), self.frame)
        self.assertEqual(get(self.database, self.roll_id), issued)
        # Re-open the station database while the head is still stopped.
        initialize_station(self.database)
        self.assertEqual(get(self.database, self.roll_id), issued)
        snapshot = recover_shared(self.database, self.roll_id, self.device)
        self.assertEqual(snapshot.frames, (self.frame,))
        self.assertEqual(find_accept_result(self.database, ('R28', 'A', 1)), None)

    def test_ordinary_sealed_roll_has_no_shared_frames(self):
        register(self.database, 'R17', self.shift, ('1',))
        accept(self.database, 'R17', Delivery('R17', 'A', 1, '1', 5, 'A1', '1,5,A1'))
        accept(self.database, 'R17', LaneEnd('R17', 'A', 1))
        accept(self.database, 'R17', LaneEnd('R17', 'B', 0))
        ordinary = seal(self.database, 'R17').roll
        self.assertEqual([row.value for row in ordinary.positions], [5])
        self.assertFalse(hasattr(ordinary, 'frames'))
        initialize_station(self.database)
        self.assertEqual(get(self.database, 'R17'), ordinary)
        self.assertEqual(get(self.database, 'R17').receipt.receipt_id, 'receipt/R17')


if __name__ == '__main__':
    unittest.main()
