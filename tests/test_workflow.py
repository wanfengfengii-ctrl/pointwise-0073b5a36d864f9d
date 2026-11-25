import json
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.issue import IssueResult
from station.archive.retrieve import get
from station.capture.recovery import find_accept_result
from station.source.recording import load_recording
from station.source.replay import initialize_station, run_recording

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def checkpoint_values(checkpoints):
    actual = []
    for checkpoint in checkpoints:
        value = checkpoint.result
        fields = {'event_index': checkpoint.event_index}
        if isinstance(value, IssueResult):
            fields.update(status=value.status, waiting_lanes=list(value.waiting_lanes))
        else:
            fields['position_statuses'] = [position.status for position in value.positions]
        actual.append(fields)
    return actual


class WorkflowTests(unittest.TestCase):
    def test_night_shift_replay(self):
        source = EXAMPLES / 'night-shift.json'
        expected = json.loads((EXAMPLES / 'night-shift.expected.json').read_text(encoding='utf-8'))
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'shift.sqlite'
            result = run_recording(database, load_recording(source))
            checkpoints = checkpoint_values(result.checkpoints)
            self.assertEqual(checkpoints, expected['checkpoints'])
            self.assertEqual(result.final.status, 'issued')
            self.assertEqual(result.final.roll, result.claimed)
            initialize_station(database)
            history = get(database, 'R17')
            self.assertEqual(history, result.claimed)
            received = find_accept_result(database, ('R17', 'B', 2))
            self.assertEqual((received.delivery.value, received.delivery.origin,
                              received.delivery.raw, received.confirmation),
                             (11, 'B2', '2,11,B2', 'released'))
        actual = json.loads(json.dumps(asdict(history), ensure_ascii=False))
        recheck = list(result.recheck_positions)
        self.assertEqual(actual, expected['roll'])
        self.assertEqual(recheck, expected['recheck_positions'])
        scenario = {
            'case_id': 'mirror',
            'input': json.loads(source.read_text(encoding='utf-8')),
            'expected': {'checkpoints': expected['checkpoints'], 'roll': expected['roll']},
            'actual': {'checkpoints': checkpoints, 'roll': actual},
            'downstream_expected': expected['recheck_positions'],
            'downstream_actual': recheck,
            'code_paths': ['station/source/replay.py', 'station/capture/intake.py',
                           'station/archive/eligibility.py', 'station/archive/roll.py',
                           'station/archive/retrieve.py'],
            'oracle': '按录制逐路手数序号。对齐后在清册各原位数等值镜像一次，列出真实出处，挑出两路均无来件的原位供复查。',
        }
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, ensure_ascii=False, separators=(',', ':')))

    def test_repeat_recording_keeps_historical_result(self):
        recording = load_recording(EXAMPLES / 'night-shift.json')
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'shift.sqlite'
            first = run_recording(database, recording)
            repeated = run_recording(database, recording)
            self.assertEqual(first.final.status, 'issued')
            self.assertEqual(repeated.final, first.final)
            self.assertEqual(repeated.claimed, first.claimed)
            self.assertEqual(repeated.recheck_positions, ('3',))
