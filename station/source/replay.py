import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from station.archive.issue import IssueResult, IssueStatus, seal
from station.archive.retrieve import get
from station.archive.roll import SealedRoll
from station.archive.store import initialize_archive
from station.capture.intake import accept
from station.capture.recovery import recover_capture
from station.capture.shared import initialize_shared
from station.capture.storage import initialize_capture
from station.preview.progress import progress
from station.preview.summary import ProgressSummary
from station.roster.register import register
from station.roster.store import Database, initialize_roster, transaction
from station.source.delivery import LANES
from station.source.events import EventKind
from station.source.recording import Recording, load_recording
from station.source.session import DeliveryReleased, offer, open_session


@dataclass(frozen=True)
class ReplayCheckpoint:
    event_index: int
    result: ProgressSummary | IssueResult


@dataclass(frozen=True)
class ReplayResult:
    checkpoints: tuple[ReplayCheckpoint, ...]
    final: IssueResult
    claimed: SealedRoll | None
    recheck_positions: tuple[str, ...]


def initialize_station(database: Database) -> None:
    with transaction(database, write=True) as connection:
        initialize_roster(connection)
        initialize_capture(connection)
        initialize_shared(connection)
        initialize_archive(connection)


def run_recording(database: Database, recording: Recording) -> ReplayResult:
    initialize_station(database)
    roll_id = recording.roll_id
    register(database, roll_id, recording.shift, recording.positions)
    sessions = {lane: open_session(recording, lane) for lane in LANES}
    checkpoints = []
    for index, event in enumerate(recording.events):
        if event.kind is EventKind.PREVIEW:
            checkpoints.append(ReplayCheckpoint(index, progress(database, roll_id)))
            continue
        if event.kind is EventKind.SEAL:
            checkpoints.append(ReplayCheckpoint(index, seal(database, roll_id)))
            continue
        session = sessions[event.lane]
        try:
            offered = offer(session)
        except DeliveryReleased:
            # A recorded redelivery cannot read the already released original.
            continue
        if offered is not None:
            accept(database, roll_id, offered, session)
        elif event.kind is EventKind.RECONNECT:
            recover_capture(database, roll_id, session)
    final = seal(database, roll_id)
    claimed = get(database, roll_id)
    recheck = () if claimed is None else tuple(
        position.position for position in claimed.positions if position.value is None
    )
    return ReplayResult(tuple(checkpoints), final, claimed, recheck)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='冷轧夜班巡检站')
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='用临时站库复演录制')
    demo.add_argument('recording', type=Path)
    run = commands.add_parser('run', help='用指定站库处理录制')
    run.add_argument('database', type=Path)
    run.add_argument('recording', type=Path)
    retrieve = commands.add_parser('get', help='按原卷号领取固定班卷')
    retrieve.add_argument('database', type=Path)
    retrieve.add_argument('roll_id')
    shared = commands.add_parser('shared', help='复演共帧录制并演示腾位与停源领卷')
    shared.add_argument('scene', type=Path)
    return parser.parse_args(argv)


def _shared_demo(args: argparse.Namespace) -> int:
    from station.source.shared_replay import (describe_claim, run_continued_branch,
                                              run_first_freed_branch)
    from station.source.shared_scene import load_scene

    scene = load_scene(args.scene)
    with TemporaryDirectory(prefix='rolling-inspection-') as directory:
        primary = run_first_freed_branch(Path(directory) / 'primary.sqlite', scene)
        continued = run_continued_branch(Path(directory) / 'continued.sqlite', scene)
    report = {
        'shared': {
            'roll_id': scene.roll_id,
            'shift': scene.shift,
            'positions': list(scene.positions),
        },
        'first_freed': {
            'steps': [asdict(step) for step in primary.steps],
            'waiting': {'status': primary.waiting.status,
                        'waiting_lanes': list(primary.waiting.waiting_lanes),
                        'waiting_frames': list(primary.waiting.waiting_frames)},
            'preview': {'waiting_lanes': list(primary.preview.waiting_lanes),
                        'waiting_frames': list(primary.preview.waiting_frames),
                        'positions': [{'position': position.position,
                                       'status': position.status,
                                       'value': position.value}
                                      for position in primary.preview.positions]},
            'final_status': primary.final.status,
            'claim': describe_claim(primary.claimed),
            'stopped_source': {
                'headers': [{
                    'lane': captured.header.lane,
                    'sequence': captured.header.sequence,
                    'origin': captured.header.origin,
                    'frame_id': captured.header.frame_id,
                    'confirmation': captured.confirmation,
                } for captured in primary.stopped_headers if captured is not None],
                'frame': None if primary.stopped_frame is None else {
                    'frame_id': primary.stopped_frame.frame_id,
                    'value': primary.stopped_frame.value,
                    'raw': primary.stopped_frame.raw,
                },
                'reopened_claim': describe_claim(primary.reopened),
            },
        },
        'continued': {
            'steps': [asdict(step) for step in continued.steps],
            'waiting': {'status': continued.waiting.status,
                        'waiting_lanes': list(continued.waiting.waiting_lanes),
                        'waiting_frames': list(continued.waiting.waiting_frames)},
            'final_status': continued.final.status,
            'claim': describe_claim(continued.claimed),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    issued = (primary.final.status is IssueStatus.ISSUED
              and continued.final.status is IssueStatus.ISSUED)
    return 0 if issued else 1


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        if args.command == 'get':
            roll = get(args.database, args.roll_id)
            print(json.dumps(None if roll is None else asdict(roll), ensure_ascii=False, indent=2))
            return 1 if roll is None else 0
        if args.command == 'shared':
            return _shared_demo(args)
        recording = load_recording(args.recording)
        if args.command == 'demo':
            with TemporaryDirectory(prefix='rolling-inspection-') as directory:
                result = run_recording(Path(directory) / 'station.sqlite', recording)
        else:
            result = run_recording(args.database, recording)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0 if result.final.status is IssueStatus.ISSUED else 1
    except (KeyError, LookupError, ValueError, OSError, sqlite3.Error) as exc:
        print(f'巡检作业失败：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
