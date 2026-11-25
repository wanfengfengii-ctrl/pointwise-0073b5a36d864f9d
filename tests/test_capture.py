import sqlite3
from dataclasses import replace

from station.archive.issue import seal
from station.archive.retrieve import get
from station.capture.evidence import ConfirmationState
from station.capture.intake import accept
from station.capture.recovery import find_accept_result, recover_capture
from station.roster.state import RosterStatus
from station.roster.store import load_roster, transaction
from station.source.delivery import Delivery, LaneEnd
from station.source.events import EventKind
from station.source.release import ReleaseOutcome, lookup_release, release_delivery
from station.source.replay import initialize_station
from station.source.session import DeliveryReleased, SourceDisconnected, disconnect, offer, read_delivery, reconnect
from tests import StationCase


class CaptureTests(StationCase):
    def test_release_reply_loss(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        session = self.source('A', EventKind.LOSE_REPLY, item)
        self.assertIsNone(offer(session))
        accepted = accept(self.database, self.roll_id, offer(session), session)
        self.assertEqual(accepted.confirmation, ConfirmationState.UNKNOWN)
        self.assertEqual(lookup_release(session, item.identity), ReleaseOutcome.RELEASED)
        with self.assertRaises(DeliveryReleased):
            read_delivery(session, 1)
        disconnect(session)
        with self.assertRaises(SourceDisconnected):
            offer(session)
        initialize_station(self.database)
        recovered = find_accept_result(self.database, item.identity)
        self.assertEqual(recovered.delivery, item)
        self.assertEqual(recovered.delivery.raw, '1,7,A1')
        self.assertEqual(accept(self.database, self.roll_id, item), recovered)
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, replace(item, value=8))
        self.assertEqual(len(recover_capture(self.database, self.roll_id).deliveries), 1)
        reconnect(session)
        snapshot = recover_capture(self.database, self.roll_id, session)
        self.assertEqual(snapshot.deliveries[0].confirmation, ConfirmationState.RELEASED)
        self.assertEqual(release_delivery(session, item.identity), ReleaseOutcome.RELEASED)
        self.assertEqual(len(snapshot.deliveries), 1)

    def test_lane_identity(self):
        a = self.stage('A', 1, '1', 7, 'A1')
        b = self.stage('B', 1, '1', 7, 'B1')
        self.assertEqual(find_accept_result(self.database, a.delivery.identity), a)
        self.assertEqual(find_accept_result(self.database, b.delivery.identity), b)
        self.assertEqual(tuple(item.delivery.origin for item in recover_capture(
            self.database, self.roll_id).deliveries), ('A1', 'B1'))

    def test_same_value_changed_origin(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        session = self.source('A', EventKind.LOSE_REPLY, item)
        offer(session)
        original = accept(self.database, self.roll_id, offer(session), session)
        disconnect(session)
        initialize_station(self.database)
        self.assertEqual(accept(self.database, self.roll_id, item), original)
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, replace(item, origin='A1b'))
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, replace(item, raw='different original text'))
        self.assertEqual(recover_capture(self.database, self.roll_id).deliveries, (original,))

    def test_end_declared_release_loss(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        self.assertEqual(seal(self.database, self.roll_id).status, 'waiting')
        self.assertIsNone(get(self.database, self.roll_id))
        later = self.delivery('B', 2, '2', 11, 'B2')
        first = self.delivery('B', 1, '1', 7, 'B1')
        session = self.source('B', LaneEnd(self.roll_id, 'B', 2), EventKind.LOSE_REPLY, later, first)
        accept(self.database, self.roll_id, offer(session), session)
        offer(session)
        accepted = accept(self.database, self.roll_id, offer(session), session)
        self.assertEqual(accepted.confirmation, ConfirmationState.UNKNOWN)
        self.assertEqual(lookup_release(session, later.identity), ReleaseOutcome.RELEASED)
        with self.assertRaises(DeliveryReleased):
            read_delivery(session, 2)
        disconnect(session)
        initialize_station(self.database)
        recovered = find_accept_result(self.database, later.identity).delivery
        self.assertEqual((recovered.value, recovered.origin, recovered.raw), (11, 'B2', '2,11,B2'))
        self.assertEqual(seal(self.database, self.roll_id).waiting_lanes, ('B',))
        self.assertIsNone(get(self.database, self.roll_id))
        reconnect(session)
        recover_capture(self.database, self.roll_id, session)
        self.assertEqual(find_accept_result(self.database, later.identity).confirmation,
                         ConfirmationState.RELEASED)
        self.assertEqual(offer(session), first)
        accept(self.database, self.roll_id, first, session)
        disconnect(session)
        result = seal(self.database, self.roll_id)
        self.assertEqual(result.status, 'issued')
        self.assertEqual([row.value for row in result.roll.positions], [7, 11, None])
        self.assertEqual([[item.origin for item in row.sources] for row in result.roll.positions],
                         [['A1', 'B1'], ['B2'], []])
        self.assertEqual(get(self.database, self.roll_id), result.roll)

    def test_capture_write_failure(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        session = self.source('A', item)
        offered = offer(session)
        with transaction(self.database, write=True) as connection:
            connection.execute('''
                CREATE TRIGGER reject_capture BEFORE INSERT ON captures
                BEGIN SELECT RAISE(ABORT, 'capture write stopped'); END
            ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'capture write stopped'):
            accept(self.database, self.roll_id, offered, session)
        self.assertEqual(read_delivery(session, 1), item)
        self.assertEqual(lookup_release(session, item.identity), ReleaseOutcome.WAITING)
        self.assertIsNone(find_accept_result(self.database, item.identity))
        with transaction(self.database, write=True) as connection:
            self.assertEqual(load_roster(connection, self.roll_id).status, RosterStatus.REGISTERED)
            connection.execute('DROP TRIGGER reject_capture')
        accepted = accept(self.database, self.roll_id, offered, session)
        self.assertEqual(accepted.confirmation, ConfirmationState.RELEASED)
        with self.assertRaises(DeliveryReleased):
            read_delivery(session, 1)

    def test_invalid_delivery_preserves_source_and_roster(self):
        with self.assertRaises(ValueError):
            Delivery(self.roll_id, 'A', 1, '1', True, 'A1', '1,7,A1')
        item = self.delivery('A', 1, '9', 7, 'A1')
        session = self.source('A', item)
        offered = offer(session)
        with self.assertRaises(ValueError):
            accept(self.database, 'OTHER', offered, session)
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, offered, session)
        with self.assertRaises(KeyError):
            accept(self.database, 'OTHER', replace(offered, roll_id='OTHER'))
        self.assertEqual(read_delivery(session, 1), item)
        self.assertEqual(recover_capture(self.database, self.roll_id).deliveries, ())

    def test_device_redelivery_until_release(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        session = self.source('A', item, item, item)
        early = accept(self.database, self.roll_id, offer(session))
        self.assertEqual(early.confirmation, ConfirmationState.PENDING)
        confirmed = accept(self.database, self.roll_id, offer(session), session)
        self.assertEqual(confirmed.confirmation, ConfirmationState.RELEASED)
        with self.assertRaises(DeliveryReleased):
            offer(session)
        self.assertEqual(recover_capture(self.database, self.roll_id).deliveries, (confirmed,))

    def test_confirmation_does_not_release_a_different_payload(self):
        item = self.delivery('A', 1, '1', 7, 'A1')
        original = accept(self.database, self.roll_id, item)
        other = replace(item, origin='A1b')
        session = self.source('A', other)
        offer(session)
        with self.assertRaises(ValueError):
            accept(self.database, self.roll_id, item, session)
        self.assertEqual(read_delivery(session, 1), other)
        self.assertEqual(find_accept_result(self.database, item.identity), original)
