from dataclasses import dataclass
from typing import TypeVar, dataclass_transform

_T = TypeVar("_T")


@dataclass_transform(frozen_default=True)
def immutable(cls: type[_T]) -> type[_T]:
    """Class decorator marking an immutable value object: a frozen, slotted dataclass."""

    return dataclass(frozen=True, slots=True)(cls)
