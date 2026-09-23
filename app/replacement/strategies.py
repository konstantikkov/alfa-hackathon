from __future__ import annotations

from app.core.enums import MaskingMode
from app.core.interfaces import MaskingStrategy
from app.core.models import PrivacyDecision, TransformResult
from app.replacement.engine import build_transform_result


class _ModeStrategy(MaskingStrategy):
    def __init__(self, mode: MaskingMode) -> None:
        self.mode = mode

    def transform(self, text: str, decisions: list[PrivacyDecision], session_seed: str) -> TransformResult:
        return build_transform_result(text, decisions, self.mode, session_seed)


PARTIAL = _ModeStrategy(MaskingMode.PARTIAL)
TOKEN = _ModeStrategy(MaskingMode.TOKEN)
SYNTHETIC = _ModeStrategy(MaskingMode.SYNTHETIC)

_STRATEGIES: dict[MaskingMode, MaskingStrategy] = {
    MaskingMode.PARTIAL: PARTIAL,
    MaskingMode.TOKEN: TOKEN,
    MaskingMode.SYNTHETIC: SYNTHETIC,
}


def get_strategy(mode: MaskingMode) -> MaskingStrategy:
    return _STRATEGIES[mode]
