from vendor.legacy_io import read_numbers  # noqa: F401  (re-exported for callers)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]
