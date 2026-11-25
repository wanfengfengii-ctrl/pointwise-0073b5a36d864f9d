from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from station.capture.intake import accept
from station.roster.register import register
from station.source.delivery import Delivery, LaneEnd
from station.source.events import EventKind, RecordedEvent
from station.source.recording import Recording
from station.source.replay import initialize_station
from station.source.session import open_session


class StationCase(unittest.TestCase):
    roll_id = 'R17'
    shift = '夜班'
    positions = ('1', '2', '3')

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'station.sqlite'
        initialize_station(self.database)
        register(self.database, self.roll_id, self.shift, self.positions)

    def delivery(self, lane, sequence, position, value, origin, *, roll_id=None):
        roll_id = self.roll_id if roll_id is None else roll_id
        return Delivery(roll_id, lane, sequence, position, value, origin,
                        f'{position},{value},{origin}')

    def stage(self, lane, sequence, position, value, origin, *, roll_id=None):
        item = self.delivery(lane, sequence, position, value, origin, roll_id=roll_id)
        return accept(self.database, item.roll_id, item)

    def end(self, lane, last_sequence, *, roll_id=None):
        roll_id = self.roll_id if roll_id is None else roll_id
        return accept(self.database, roll_id, LaneEnd(roll_id, lane, last_sequence))

    def source(self, lane, *events):
        recorded = []
        for event in events:
            if isinstance(event, Delivery):
                recorded.append(RecordedEvent(EventKind.DELIVERY, lane, event))
            elif isinstance(event, LaneEnd):
                recorded.append(RecordedEvent(EventKind.END, lane, event))
            else:
                recorded.append(RecordedEvent(event, lane))
        recording = Recording(self.roll_id, self.shift, self.positions, tuple(recorded))
        return open_session(recording, lane)

    def complete_mirror(self):
        self.stage('B', 2, '2', 11, 'B2')
        self.stage('A', 1, '1', 7, 'A1')
        self.stage('B', 1, '1', 7, 'B1')
        self.end('A', 1)
        self.end('B', 2)
