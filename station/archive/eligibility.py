from dataclasses import dataclass

from station.capture.evidence import CaptureSnapshot, lane_is_covered
from station.capture.shared import SharedSnapshot, lane_headers_covered, waiting_frame_ids
from station.source.delivery import LANES


@dataclass(frozen=True)
class SealEligibility:
    waiting_lanes: tuple[str, ...]
    waiting_frames: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.waiting_lanes and not self.waiting_frames


def assess_eligibility(snapshot: CaptureSnapshot) -> SealEligibility:
    waiting = tuple(lane for lane in LANES if not lane_is_covered(snapshot, lane))
    return SealEligibility(waiting)


def assess_shared_eligibility(snapshot: SharedSnapshot) -> SealEligibility:
    waiting_lanes = tuple(lane for lane in LANES if not lane_headers_covered(snapshot, lane))
    # A captured header may point to a body the device still owes.
    return SealEligibility(waiting_lanes, waiting_frame_ids(snapshot))
