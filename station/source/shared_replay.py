"""Drive the shared-frame night shift against the supplied recording head."""
from dataclasses import dataclass

from examples.shared_frame_device import Frame, FrameDevice
from station.archive.issue import IssueResult, seal
from station.archive.retrieve import get
from station.archive.roll import SealedRoll
from station.capture.intake import accept, accept_frame, accept_header
from station.capture.recovery import find_frame, find_header
from station.preview.progress import progress
from station.preview.summary import ProgressSummary
from station.roster.register import register_shared
from station.roster.store import Database
from station.source.delivery import LaneEnd
from station.source.replay import initialize_station
from station.source.shared_scene import SharedGroup, SharedScene


@dataclass(frozen=True)
class SlotRecord:
    step: str
    slots: dict[str, str | None]
    note: str


@dataclass(frozen=True)
class SharedReplay:
    steps: tuple[SlotRecord, ...]
    waiting: IssueResult
    preview: ProgressSummary
    final: IssueResult
    claimed: SealedRoll | None
    reopened: SealedRoll | None
    stopped_headers: tuple[object, ...]
    stopped_frame: Frame | None


def _slots(device: FrameDevice) -> dict[str, str | None]:
    return {lane: (None if (header := device.read_header(lane)) is None else header.origin)
            for lane in ('A', 'B')}


def _note(steps: list[SlotRecord], device: FrameDevice, step: str, note: str) -> None:
    steps.append(SlotRecord(step, _slots(device), note))


def _declare_ends(database: Database, scene: SharedScene, last: dict[str, int]) -> None:
    for lane in ('A', 'B'):
        accept(database, scene.roll_id, LaneEnd(scene.roll_id, lane, last[lane]))


def _capture_body(database: Database, scene: SharedScene, device: FrameDevice,
                  group: SharedGroup) -> Frame:
    return accept_frame(database, device.read_frame(scene.roll_id, group.frame.frame_id), device)


def run_first_freed_branch(database: Database, scene: SharedScene) -> SharedReplay:
    """One lane frees its slot before the other lane delivers; the body arrives last."""
    initialize_station(database)
    register_shared(database, scene.roll_id, scene.shift, scene.positions)
    device = FrameDevice()
    shared = scene.shared
    device.stage(shared.frame, *shared.headers)
    device.set_body_online(False)
    steps: list[SlotRecord] = []
    _note(steps, device, 'staged', 'F staged with two headers; body channel down')
    _declare_ends(database, scene, {'A': 1, 'B': 1})

    first, second = shared.headers
    accept_header(database, first, device)
    _note(steps, device, f'header {first.origin}',
          'first header durable; its slot frees without the other lane delivering')
    accept_header(database, second, device)
    _note(steps, device, f'header {second.origin}',
          'last slot frees; station keeps a hold on F until its body is durable')

    waiting = seal(database, scene.roll_id)
    preview = progress(database, scene.roll_id)
    device.set_body_online(True)
    _capture_body(database, scene, device, shared)
    _note(steps, device, 'frame F', 'body durable; station hold cleared')
    final = seal(database, scene.roll_id)
    claimed = get(database, scene.roll_id)

    # Source fully stops: only durable station results remain queryable.
    device.set_online(False)
    stopped_headers = tuple(find_header(database, header.identity) for header in shared.headers)
    stopped_frame = find_frame(database, scene.roll_id, shared.frame.frame_id)
    initialize_station(database)
    reopened = get(database, scene.roll_id)
    return SharedReplay(tuple(steps), waiting, preview, final, claimed, reopened,
                        stopped_headers, stopped_frame)


def run_continued_branch(database: Database, scene: SharedScene) -> SharedReplay:
    """The first freed slot carries a later frame G; G and F share value and raw text."""
    if scene.next_a is None:
        raise ValueError('scene needs next_a for the continued branch')
    initialize_station(database)
    register_shared(database, scene.roll_id, scene.shift, scene.positions)
    device = FrameDevice()
    shared, next_a = scene.shared, scene.next_a
    device.stage(shared.frame, *shared.headers)
    device.set_body_online(False)
    steps: list[SlotRecord] = []
    _note(steps, device, 'staged', 'F staged with two headers; body channel down')
    _declare_ends(database, scene, {'A': 2, 'B': 1})

    accept_header(database, shared.headers[0], device)
    _note(steps, device, 'header A1', 'A slot frees while B still owns F')
    device.stage(next_a.frame, *next_a.headers)
    _note(steps, device, 'staged G', 'later frame G takes the freed A slot')
    accept_header(database, next_a.headers[0], device)
    _note(steps, device, 'header A2', 'G is held until its own body arrives')
    device.set_body_online(True)
    _capture_body(database, scene, device, next_a)
    _note(steps, device, 'frame G', 'G durable; F is still owed')
    accept_header(database, shared.headers[1], device)
    _note(steps, device, 'header B1', 'last slot frees; station keeps a hold on F')
    waiting = seal(database, scene.roll_id)
    preview = progress(database, scene.roll_id)
    _capture_body(database, scene, device, shared)
    _note(steps, device, 'frame F', 'F durable; both referenced frames present')
    final = seal(database, scene.roll_id)
    claimed = get(database, scene.roll_id)
    return SharedReplay(tuple(steps), waiting, preview, final, claimed, claimed, (), None)


def describe_claim(claimed: SealedRoll | None) -> dict:
    if claimed is None:
        return {'claimed': None}
    positions = [{
        'position': position.position,
        'value': position.value,
        'sources': [{
            'lane': source.lane,
            'sequence': source.sequence,
            'value': source.value,
            'origin': source.origin,
            'frame_id': getattr(source, 'frame_id', None),
        } for source in position.sources],
    } for position in claimed.positions]
    frames = [{'frame_id': frame_id, 'value': frame.value, 'raw': frame.raw}
              for frame_id, frame in (getattr(claimed, 'frames', None) or {}).items()]
    return {
        'roll_id': claimed.roll_id,
        'shift': claimed.shift,
        'positions': positions,
        'measured': claimed.measured,
        'missing': claimed.missing,
        'receipt': {'roll_id': claimed.receipt.roll_id,
                    'receipt_id': claimed.receipt.receipt_id},
        'frames': frames,
    }
