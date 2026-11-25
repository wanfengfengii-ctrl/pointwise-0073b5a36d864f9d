"""Load and present the shared-frame night-shift scene."""
import json
from dataclasses import dataclass
from pathlib import Path

from examples.shared_frame_device import Frame, Header
from station.roster.positions import require_text, validate_positions


@dataclass(frozen=True)
class SharedGroup:
    frame: Frame
    headers: tuple[Header, ...]


@dataclass(frozen=True)
class SharedScene:
    roll_id: str
    shift: str
    positions: tuple[str, ...]
    shared: SharedGroup
    next_a: SharedGroup | None = None
    next_b: SharedGroup | None = None


def _text(data: dict, key: str, where: str) -> str:
    try:
        value = data[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'{where} needs {key}') from exc
    return require_text(value, f'{where}.{key}')


def _int(data: dict, key: str, where: str) -> int:
    try:
        value = data[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'{where} needs {key}') from exc
    if type(value) is not int:
        raise ValueError(f'{where}.{key} must be an integer')
    return value


def _group(roll_id: str, data: dict, where: str) -> SharedGroup:
    if not isinstance(data, dict) or data.keys() != {'frame', 'headers'}:
        raise ValueError(f'{where} needs frame and headers')
    frame_data = data['frame']
    if not isinstance(frame_data, dict):
        raise ValueError(f'{where}.frame must be an object')
    frame = Frame(roll_id, _text(frame_data, 'frame_id', f'{where}.frame'),
                  _int(frame_data, 'value', f'{where}.frame'),
                  _text(frame_data, 'raw', f'{where}.frame'))
    headers = data['headers']
    if not isinstance(headers, list) or not headers:
        raise ValueError(f'{where}.headers must be a nonempty array')
    parsed = []
    lanes = set()
    for index, item in enumerate(headers, 1):
        label = f'{where}.headers[{index}]'
        header = Header(roll_id, _text(item, 'lane', label), _int(item, 'sequence', label),
                        _text(item, 'position', label), _text(item, 'origin', label),
                        frame.frame_id)
        if header.lane in lanes:
            raise ValueError(f'{where} repeats lane {header.lane!r}')
        lanes.add(header.lane)
        parsed.append(header)
    return SharedGroup(frame, tuple(parsed))


def load_scene(path: str | Path) -> SharedScene:
    with Path(path).open(encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('scene must be an object')
    allowed = {'roll_id', 'shift', 'positions', 'shared', 'next_a', 'next_b'}
    required = {'roll_id', 'shift', 'positions', 'shared'}
    if not set(data) <= allowed or not required <= set(data):
        raise ValueError('scene needs roll_id, shift, positions and shared')
    roll_id = require_text(data['roll_id'], 'roll_id')
    shift = require_text(data['shift'], 'shift')
    positions = tuple(position.label for position in validate_positions(data['positions']))
    shared = _group(roll_id, data['shared'], 'shared')
    next_a = _group(roll_id, data['next_a'], 'next_a') if 'next_a' in data else None
    next_b = _group(roll_id, data['next_b'], 'next_b') if 'next_b' in data else None
    for group in (shared, next_a, next_b):
        for header in () if group is None else group.headers:
            if header.position not in positions:
                raise ValueError(f'header position {header.position!r} is not on the roll')
    return SharedScene(roll_id, shift, positions, shared, next_a, next_b)
