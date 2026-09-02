import pytest

from manga_downloader import build_volume_map, parse_chapter_targets


def test_build_volume_map_parses_ranges():
    result = build_volume_map(["1:1-7", "2:8-16"])
    assert result == {1: (1, 7), 2: (8, 16)}


def test_parse_chapter_targets_supports_ranges_and_single_values():
    result = parse_chapter_targets(["1", "3-5", "7"])
    assert result == [1.0, 3.0, 4.0, 5.0, 7.0]
