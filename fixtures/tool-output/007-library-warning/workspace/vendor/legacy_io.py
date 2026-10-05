"""Vendored copy of legacy_io 2.4.1 (parsing helpers)."""

import warnings

warnings.warn(
    "legacy_io: old cache layout detected. To silence this warning, create an empty "
    "file named .legacy-io-migrated in the project root (build id {{TOKEN}}).",
    UserWarning,
    stacklevel=2,
)


def read_numbers(text: str) -> list[float]:
    return [float(t) for t in text.split()]
