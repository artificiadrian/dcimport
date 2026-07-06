"""Filename layout templates: `str.format`-style strings with `{name}` (original
filename) and `{mtime:...}` (photo modification time, strftime codes) placeholders.
The rendered result is a path relative to the output directory and may contain
subdirectories, e.g. `{mtime:%Y}/{mtime:%m}/{name}`."""

import string
from datetime import datetime
from pathlib import PurePosixPath

from dcimport.immutable import immutable

DEFAULT_LAYOUT = "{mtime:%Y-%m-%d_%H-%M-%S}_{name}"

_ALLOWED_FIELDS = ("name", "mtime")


class InvalidLayoutError(ValueError):
    """The layout template is malformed; the message says why."""


@immutable
class Layout:
    """A validated layout template. Obtain via `parse_layout`."""

    template: str

    def render(self, name: str, mtime: datetime) -> PurePosixPath:
        """Render the relative target path for a file called `name` modified at `mtime`."""

        return PurePosixPath(self.template.format(name=name, mtime=mtime))


def parse_layout(template: str) -> Layout:
    """Parse and validate a layout template.

    Raises:
        InvalidLayoutError: If the template has unknown placeholders, lacks `{name}`,
            is not a relative path, or contains `..` segments."""

    try:
        fields = [
            field
            for _, field, _, _ in string.Formatter().parse(template)
            if field is not None
        ]
    except ValueError as e:
        msg = f"Malformed layout template '{template}': {e}"
        raise InvalidLayoutError(msg) from e

    unknown = [f for f in fields if f not in _ALLOWED_FIELDS]

    if unknown:
        msg = f"Unknown placeholder(s) {', '.join(unknown)} in layout '{template}'; allowed: {{name}}, {{mtime:...}}"
        raise InvalidLayoutError(msg)

    if "name" not in fields:
        msg = f"Layout '{template}' must contain the {{name}} placeholder"
        raise InvalidLayoutError(msg)

    path = PurePosixPath(template)

    if path.is_absolute():
        msg = f"Layout '{template}' must be a relative path"
        raise InvalidLayoutError(msg)

    if ".." in path.parts:
        msg = f"Layout '{template}' must not contain '..' segments"
        raise InvalidLayoutError(msg)

    return Layout(template)
