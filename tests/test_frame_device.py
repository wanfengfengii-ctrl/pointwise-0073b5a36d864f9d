import json
import sqlite3
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from examples.shared_frame_device import Frame, FrameDevice, Header
from station.source.release import ReleaseReplyLost
from station.source.session import SourceDisconnected


@contextmanager
def reject_next_write_commit():
    "Reject one SQLite commit after a connection has written rows."
    connect = sqlite3.connect
    rejected = False

    def connect_with_fault(*args, **kwargs):
        connection = connect(*args, **kwargs)

        def authorize(action, name, _arg, _database, _trigger):
            nonlocal rejected
            if (not rejected and action == sqlite3.SQLITE_TRANSACTION
                    and name == "COMMIT" and connection.total_changes):
                rejected = True
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    with patch.object(sqlite3, "connect", side_effect=connect_with_fault):
        yield


class FrameDeviceTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "shared-frame.json"
        self.scene = json.loads(path.read_text(encoding="utf-8"))
        self.device = FrameDevice()
        self.frame, (self.a, self.b) = self.stage("shared")

    def stage(self, name):
        group = self.scene[name]
        roll_id = self.scene["roll_id"]
        frame = Frame(roll_id, **group["frame"])
        headers = tuple(Header(roll_id, frame_id=frame.frame_id, **h) for h in group["headers"])
        self.device.stage(frame, *headers)
        return frame, headers

    def test_slot_reuse_after_lost_reply(self):
        self.device.set_body_online(False)
        self.assertEqual(self.device.read_header("A"), self.a)
        with self.assertRaises(SourceDisconnected):
            self.device.read_frame(*self.frame.key)
        with self.assertRaises(BlockingIOError):
            self.stage("next_a")
        self.device.lose_next_return_reply()
        with self.assertRaises(ReleaseReplyLost):
            self.device.return_slot(self.a)
        self.assertEqual(self.device.returned_header(self.a.identity), self.a)
        self.assertIsNone(self.device.read_header("A"))
        self.assertEqual(self.device.read_header("B"), self.b)
        other, (next_header,) = self.stage("next_a")
        with self.assertRaises(ValueError):
            self.device.return_slot(self.a)
        self.assertEqual(self.device.read_header("A"), next_header)
        self.device.set_body_online(True)
        self.assertEqual(self.device.read_frame(*self.frame.key), self.frame)
        self.assertEqual(self.device.read_frame(*other.key), other)
        self.assertEqual((self.frame.value, self.frame.raw), (other.value, other.raw))
        self.assertNotEqual(self.frame.key, other.key)

    def test_raw_returns_can_destroy_uncollected_body(self):
        self.device.set_body_online(False)
        self.assertIn("B", self.device.references(*self.frame.key))
        self.assertIn("A", self.device.references(*self.frame.key))
        self.device.return_slot(self.a)
        self.device.return_slot(self.b)
        self.device.set_body_online(True)
        with self.assertRaises(KeyError):
            self.device.read_frame(*self.frame.key)

    def test_raw_hold_is_independent_of_both_slots(self):
        self.device.set_body_online(False)
        with self.device.serialized:
            self.device.hold_frame(*self.frame.key)
            self.device.return_slot(self.b)
            self.device.return_slot(self.a)
        self.assertEqual(self.device.references(*self.frame.key), {"hold"})
        self.device.set_body_online(True)
        self.assertEqual(self.device.read_frame(*self.frame.key), self.frame)
        self.device.drop_hold(*self.frame.key)
        with self.assertRaises(KeyError):
            self.device.read_frame(*self.frame.key)


class CommitFaultTests(unittest.TestCase):
    def test_write_commit_failure_rolls_back_and_can_retry(self):
        with reject_next_write_commit(), closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
            with self.assertRaises(sqlite3.DatabaseError), connection:
                connection.execute("INSERT INTO sample VALUES (7)")
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchall(), [])
            with connection:
                connection.execute("INSERT INTO sample VALUES (7)")
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchall(), [(7,)])
