from collections.abc import Iterable
from dataclasses import dataclass


def require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be non-blank text')
    return value


@dataclass(frozen=True)
class Position:
    label: str

    def __post_init__(self) -> None:
        require_text(self.label, 'position')


def validate_positions(positions: Iterable[str | Position]) -> tuple[Position, ...]:
    if isinstance(positions, (str, bytes)):
        raise ValueError('positions must be an ordered collection')
    try:
        ordered = tuple(item if isinstance(item, Position) else Position(item) for item in positions)
    except TypeError as exc:
        raise ValueError('positions must be an ordered collection') from exc
    if not ordered or len(set(ordered)) != len(ordered):
        raise ValueError('positions must be nonempty and unique within the roll')
    return ordered
