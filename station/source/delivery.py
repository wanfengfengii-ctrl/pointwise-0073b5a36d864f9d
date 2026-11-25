from dataclasses import dataclass

from station.roster.positions import require_text

LANES = ('A', 'B')
DeliveryIdentity = tuple[str, str, int]


def validate_lane(lane: str) -> None:
    if lane not in LANES:
        raise ValueError(f'unknown lane {lane!r}')


def validate_sequence(sequence: int, *, minimum: int) -> None:
    if type(sequence) is not int or sequence < minimum:
        raise ValueError(f'sequence must be an integer >= {minimum}')


@dataclass(frozen=True)
class Delivery:
    roll_id: str
    lane: str
    sequence: int
    position: str
    value: int
    origin: str
    raw: str

    def __post_init__(self) -> None:
        require_text(self.roll_id, 'roll_id')
        validate_lane(self.lane)
        validate_sequence(self.sequence, minimum=1)
        require_text(self.position, 'position')
        require_text(self.origin, 'origin')
        if type(self.value) is not int:
            raise ValueError('value must be an integer')
        if not isinstance(self.raw, str) or self.raw == '':
            raise ValueError('raw must be nonempty text')

    @property
    def identity(self) -> DeliveryIdentity:
        return self.roll_id, self.lane, self.sequence


@dataclass(frozen=True)
class LaneEnd:
    roll_id: str
    lane: str
    last_sequence: int

    def __post_init__(self) -> None:
        require_text(self.roll_id, 'roll_id')
        validate_lane(self.lane)
        validate_sequence(self.last_sequence, minimum=0)
