from station.roster.positions import Position
from station.roster.register import register
from station.roster.state import RosterStatus
from station.roster.store import load_roster, transaction
from station.source.replay import initialize_station
from tests import StationCase


class RosterTests(StationCase):
    def test_repeat_registration(self):
        initial = register(self.database, self.roll_id, self.shift, self.positions)
        initialize_station(self.database)
        self.assertEqual(initial, register(self.database, self.roll_id, self.shift, self.positions))
        with self.assertRaises(ValueError):
            register(self.database, self.roll_id, self.shift, ('1', '2', '4'))
        with self.assertRaises(ValueError):
            register(self.database, self.roll_id, '白班', self.positions)
        with transaction(self.database) as connection:
            self.assertEqual(load_roster(connection, self.roll_id), initial)

    def test_init_preserves_order(self):
        ordered = (Position('3'), Position('1'), Position('2'))
        register(self.database, 'ORDER', self.shift, ordered)
        initialize_station(self.database)
        with self.assertRaises(ValueError):
            register(self.database, 'ORDER', self.shift, ('1', '2', '3'))
        self.assertEqual(register(self.database, 'ORDER', self.shift, ('3', '1', '2')).positions,
                         ordered)

    def test_invalid_roster_keeps_number_available(self):
        for labels in ((), ('1', '1'), (' ',), (1,), '12'):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                register(self.database, 'NEW', self.shift, labels)
        created = register(self.database, 'NEW', self.shift, ('测厚', '边缘'))
        self.assertEqual(tuple(item.label for item in created.positions), ('测厚', '边缘'))
        self.assertEqual(created.status, RosterStatus.REGISTERED)

    def test_first_end_starts_capture_and_keeps_roster(self):
        self.end('A', 0)
        initialize_station(self.database)
        roster = register(self.database, self.roll_id, self.shift, self.positions)
        self.assertEqual(roster.status, RosterStatus.CAPTURE_STARTED)
        self.assertEqual(tuple(position.label for position in roster.positions), self.positions)
