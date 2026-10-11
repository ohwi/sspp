"""Interpret native preset targets for display without changing stored levels."""


def _checked_level(level: int) -> int:
    if type(level) is not int or not 0 <= level <= 6:
        raise ValueError("게임 프리셋 목표 레벨은 0~6의 정수여야 합니다.")
    return level


def target_label(level: int) -> str:
    level = _checked_level(level)
    if level == 0:
        return "목표 제외"
    if level == 1:
        return "Lv.1 확보"
    if level == 6:
        return "목표 Lv.6"
    return "다다익선"


def target_description(level: int) -> str:
    level = _checked_level(level)
    if 2 <= level <= 5:
        return f"다다익선 (프리셋 Lv.{level})"
    return target_label(level)
