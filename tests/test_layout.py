from datetime import datetime

import pytest

from dcimport.layout import DEFAULT_LAYOUT, InvalidLayoutError, parse_layout

MTIME = datetime(2024, 1, 2, 3, 4, 5)


def test_default_layout_renders_timestamped_name():
    layout = parse_layout(DEFAULT_LAYOUT)

    rendered = layout.render(name="IMG_0001.JPG", mtime=MTIME)

    assert str(rendered) == "2024-01-02_03-04-05_IMG_0001.JPG"


def test_layout_with_subdirectories():
    layout = parse_layout("{mtime:%Y}/{mtime:%m}/{name}")

    rendered = layout.render(name="IMG_0001.JPG", mtime=MTIME)

    assert rendered.parts == ("2024", "01", "IMG_0001.JPG")


def test_layout_without_name_placeholder_is_rejected():
    with pytest.raises(InvalidLayoutError, match=r"\{name\}"):
        parse_layout("{mtime:%Y}/photos")


def test_layout_with_unknown_placeholder_is_rejected():
    with pytest.raises(InvalidLayoutError, match="foo"):
        parse_layout("{foo}_{name}")


def test_absolute_layout_is_rejected():
    with pytest.raises(InvalidLayoutError, match="relative"):
        parse_layout("/photos/{name}")


def test_parent_traversal_is_rejected():
    with pytest.raises(InvalidLayoutError, match=r"\.\."):
        parse_layout("../{name}")
