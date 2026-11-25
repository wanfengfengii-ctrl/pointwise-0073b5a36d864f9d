from station.archive.retrieve import get
from station.preview.progress import progress
from tests import StationCase


class PreviewTests(StationCase):
    def test_one_lane_ended(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        view = progress(self.database, self.roll_id)
        self.assertEqual(view.waiting_lanes, ('B',))
        self.assertEqual([row.status for row in view.positions], ['observed', 'pending', 'pending'])
        self.assertEqual([row.value for row in view.positions], [7, None, None])
        self.assertEqual(view.positions[0].sources[0].origin, 'A1')
        self.assertEqual((view.observed, view.pending, view.missing), (1, 2, 0))
        self.assertIsNone(get(self.database, self.roll_id))

    def test_late_conflict_refresh(self):
        self.stage('A', 1, '1', 7, 'A1')
        self.end('A', 1)
        earlier = progress(self.database, self.roll_id)
        self.assertEqual(earlier.positions[0].value, 7)
        self.assertEqual(earlier.conflicts, ())
        self.stage('B', 1, '1', 9, 'B1')
        current = progress(self.database, self.roll_id)
        self.assertEqual([row.status for row in current.positions], ['conflict', 'pending', 'pending'])
        self.assertIsNone(current.positions[0].value)
        self.assertEqual(current.conflicts[0].position, '1')
        self.assertEqual(current.conflicts[0].values, (7, 9))
        self.assertEqual([item.origin for item in current.conflicts[0].sources], ['A1', 'B1'])
        self.assertIsNone(get(self.database, self.roll_id))

    def test_unknown_roll(self):
        with self.assertRaises(KeyError):
            progress(self.database, 'OTHER')
