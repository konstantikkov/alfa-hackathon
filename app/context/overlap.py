from __future__ import annotations

from app.core.models import Candidate


def resolve_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    """Pick one candidate per overlapping span cluster.

    A bare 10-digit run can look like both a PHONE and an INN candidate at the same
    position -- that's fine, detectors are deliberately blind to each other. This is
    the single place spans get reconciled: highest confidence wins, ties broken by the
    longer (more specific) span, then by earliest start for determinism.
    """
    if not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda c: (-c.detector_confidence, -(c.end - c.start), c.start),
    )

    chosen: list[Candidate] = []
    occupied: list[tuple[int, int]] = []
    for candidate in ordered:
        if any(candidate.start < end and start < candidate.end for start, end in occupied):
            continue
        chosen.append(candidate)
        occupied.append((candidate.start, candidate.end))

    return sorted(chosen, key=lambda c: c.start)
