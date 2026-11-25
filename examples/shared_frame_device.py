from dataclasses import dataclass
from threading import RLock

from station.roster.positions import require_text
from station.source.delivery import DeliveryIdentity, validate_lane, validate_sequence
from station.source.release import ReleaseReplyLost
from station.source.session import SourceDisconnected


@dataclass(frozen=True)
class Header:
    roll_id: str
    lane: str
    sequence: int
    position: str
    origin: str
    frame_id: str

    def __post_init__(self):
        validate_lane(self.lane)
        validate_sequence(self.sequence, minimum=1)
        for field in ("roll_id", "position", "origin", "frame_id"):
            require_text(getattr(self, field), field)

    @property
    def identity(self) -> DeliveryIdentity:
        return self.roll_id, self.lane, self.sequence

    @property
    def frame_key(self) -> tuple[str, str]:
        return self.roll_id, self.frame_id


@dataclass(frozen=True)
class Frame:
    roll_id: str
    frame_id: str
    value: int
    raw: str

    def __post_init__(self):
        require_text(self.roll_id, "roll_id")
        require_text(self.frame_id, "frame_id")
        if type(self.value) is not int:
            raise ValueError("frame value must be an integer")
        if not isinstance(self.raw, str) or self.raw == "":
            raise ValueError("frame raw must be nonempty text")

    @property
    def key(self) -> tuple[str, str]:
        return self.roll_id, self.frame_id


class FrameDevice:
    "A recording head with two one-entry output slots and a raw hold command."

    def __init__(self):
        self.serialized = RLock()
        self._online = True
        self._body_online = True
        self._lose_reply = False
        self._slots: dict[str, Header] = {}
        self._frames: dict[tuple[str, str], Frame] = {}
        self._seen_frames: set[tuple[str, str]] = set()
        self._holds: set[tuple[str, str]] = set()
        self._returned: dict[DeliveryIdentity, Header] = {}

    def _require_online(self):
        if not self._online:
            raise SourceDisconnected("recording head is offline")

    def set_online(self, online: bool):
        with self.serialized:
            self._online = online

    def set_body_online(self, online: bool):
        with self.serialized:
            self._body_online = online

    def lose_next_return_reply(self):
        with self.serialized:
            self._lose_reply = True

    def stage(self, frame: Frame, *headers: Header):
        "Place a new physical frame and its output headers on the device."
        with self.serialized:
            lanes = {header.lane for header in headers}
            if not headers or len(lanes) != len(headers):
                raise ValueError("each staged frame needs one header per participating lane")
            if frame.key in self._seen_frames or any(
                h.frame_key != frame.key or h.identity in self._returned for h in headers
            ):
                raise ValueError("staging changes an existing identity or uses another frame")
            if lanes.intersection(self._slots):
                raise BlockingIOError("device header slot occupied")
            self._seen_frames.add(frame.key)
            self._frames[frame.key] = frame
            for header in headers:
                self._slots[header.lane] = header

    def read_header(self, lane: str) -> Header | None:
        with self.serialized:
            self._require_online()
            validate_lane(lane)
            return self._slots.get(lane)

    def read_frame(self, roll_id: str, frame_id: str) -> Frame:
        with self.serialized:
            self._require_online()
            if not self._body_online:
                raise SourceDisconnected("frame body channel is offline")
            return self._frames[roll_id, frame_id]

    def references(self, roll_id: str, frame_id: str) -> frozenset[str]:
        with self.serialized:
            self._require_online()
            return self._references((roll_id, frame_id))

    def _references(self, key):
        refs = {lane for lane, header in self._slots.items() if header.frame_key == key}
        if key in self._holds:
            refs.add("hold")
        return frozenset(refs)

    def _collect(self, key):
        if not self._references(key):
            self._frames.pop(key, None)

    def hold_frame(self, roll_id: str, frame_id: str):
        with self.serialized:
            self._require_online()
            key = roll_id, frame_id
            if key not in self._frames:
                raise KeyError(key)
            self._holds.add(key)

    def drop_hold(self, roll_id: str, frame_id: str):
        with self.serialized:
            self._require_online()
            key = roll_id, frame_id
            self._holds.discard(key)
            self._collect(key)

    def returned_header(self, identity: DeliveryIdentity) -> Header | None:
        with self.serialized:
            self._require_online()
            return self._returned.get(identity)

    def return_slot(self, expected: Header):
        "Perform a physical return, with no knowledge of the station database."
        with self.serialized:
            self._require_online()
            if self._slots.get(expected.lane) != expected:
                raise ValueError("slot no longer contains the requested header")
            del self._slots[expected.lane]
            self._returned[expected.identity] = expected
            self._collect(expected.frame_key)
            if self._lose_reply:
                self._lose_reply = False
                raise ReleaseReplyLost(f"slot returned before reply: {expected.identity!r}")
