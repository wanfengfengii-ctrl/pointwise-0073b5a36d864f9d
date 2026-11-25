"""Run the original regression, then check the shared-frame night shift explicitly."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import check_project
from station.archive.issue import IssueStatus
from station.source.shared_replay import (describe_claim, run_continued_branch,
                                          run_first_freed_branch)
from station.source.shared_scene import load_scene

EXAMPLES = Path(__file__).resolve().parent / 'examples'


class SharedCheckError(AssertionError):
    pass


def _check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def check_first_freed(primary, failures: list[str]) -> None:
    staged, first_header, second_header, frame_step = primary.steps
    _check(staged.slots == {'A': 'A1', 'B': 'B1'}, 'both headers must start in their slots', failures)
    freed = {lane for lane, origin in first_header.slots.items() if origin is None}
    _check(len(freed) == 1, 'the first captured header must free its own slot only', failures)
    _check(staged.slots != first_header.slots, 'the first return must change the slots', failures)
    _check(primary.waiting.status == IssueStatus.WAITING.value or
           primary.waiting.status == IssueStatus.WAITING,
           'sealing before the body must still wait', failures)
    _check(tuple(primary.waiting.waiting_lanes) == (),
           'both lane ends and headers should be complete', failures)
    _check(tuple(primary.waiting.waiting_frames) == ('F',),
           'the referenced body F must remain owed', failures)
    _check(second_header.slots == {'A': None, 'B': None},
           'the last header return must empty both slots', failures)
    _check(primary.final.status in (IssueStatus.ISSUED, 'issued'),
           'the completed shared roll must be issued', failures)
    _check(frame_step.step == 'frame F', 'the body must arrive after the headers', failures)

    claim = describe_claim(primary.claimed)
    position = claim['positions'][0] if claim.get('positions') else {}
    _check(position.get('value') == 7, 'position 1 must read 7', failures)
    _check([source['origin'] for source in position.get('sources', [])] == ['A1', 'B1'],
           'position 1 must keep both lane origins', failures)
    _check({source['frame_id'] for source in position.get('sources', [])} == {'F'},
           'both origins must point at frame F', failures)
    _check(claim.get('frames') == [{'frame_id': 'F', 'value': 7, 'raw': 'f7'}],
           'the claim must carry the deduplicated frame F body', failures)
    _check(claim['positions'][1]['value'] is None, 'position 2 must stay missing', failures)

    reopened = describe_claim(primary.reopened)
    _check(reopened == claim, 'reopening the station must return the same receipt', failures)
    stopped = {
        'headers': primary.stopped_headers,
        'frame': primary.stopped_frame,
    }
    origins = [captured.header.origin for captured in stopped['headers'] if captured is not None]
    _check(origins == ['A1', 'B1'],
           'the stopped source must still resolve both header origins', failures)
    frame = stopped['frame']
    _check(frame is not None and (frame.value, frame.raw) == (7, 'f7'),
           'the stopped source must still return F raw text f7', failures)


def check_continued(continued, failures: list[str]) -> None:
    _check(continued.waiting.waiting_frames == ('F',),
           'a later frame G must not satisfy the owed body F', failures)
    _check(continued.final.status in (IssueStatus.ISSUED, 'issued'),
           'the continued shared roll must be issued', failures)
    claim = describe_claim(continued.claimed)
    frames = {frame['frame_id']: frame for frame in claim.get('frames', [])}
    _check(set(frames) == {'F', 'G'}, 'both distinct frames must survive to the claim', failures)
    _check(frames.get('F', {}).get('raw') == 'f7' and frames.get('G', {}).get('raw') == 'f7',
           'frames sharing text must still be stored separately', failures)
    origins = [source['origin'] for source in claim['positions'][0]['sources']]
    _check(origins == ['A1', 'A2', 'B1'],
           'the continued branch must keep all three origins in lane order', failures)


def check_shared_job(failures: list[str]) -> None:
    scene = load_scene(EXAMPLES / 'shared-frame.json')
    with TemporaryDirectory(prefix='rolling-inspection-check-') as directory:
        base = Path(directory)
        primary = run_first_freed_branch(base / 'primary.sqlite', scene)
        continued = run_continued_branch(base / 'continued.sqlite', scene)
    check_first_freed(primary, failures)
    check_continued(continued, failures)


def main() -> int:
    regression = check_project.main()
    if regression:
        return regression
    failures: list[str] = []
    try:
        check_shared_job(failures)
    except Exception as exc:  # noqa: BLE001 - report any shared-check failure as a check failure.
        failures.append(f'shared-frame check raised {type(exc).__name__}: {exc}')
    for failure in failures:
        print(f'SHARED CHECK FAILED {failure}', file=sys.stderr)
    print(f'SHARED CHECK SUMMARY checks={2} failures={len(failures)} '
          f'result={"PASS" if not failures else "FAIL"}')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
