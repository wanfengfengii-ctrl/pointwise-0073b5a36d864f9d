from dataclasses import dataclass

from station.capture.evidence import CaptureSnapshot, lane_is_covered
from station.source.delivery import LANES


@dataclass(frozen=True)
class SealEligibility:
    waiting_lanes: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.waiting_lanes


def assess_eligibility(snapshot: CaptureSnapshot) -> SealEligibility:
    waiting = tuple(lane for lane in LANES if not lane_is_covered(snapshot, lane))
    return SealEligibility(waiting)
