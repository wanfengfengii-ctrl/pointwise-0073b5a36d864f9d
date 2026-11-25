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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        if args.command == 'get':
            roll = get(args.database, args.roll_id)
            print(json.dumps(None if roll is None else asdict(roll), ensure_ascii=False, indent=2))
            return 1 if roll is None else 0
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
