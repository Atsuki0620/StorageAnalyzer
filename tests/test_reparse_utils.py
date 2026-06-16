from types import SimpleNamespace

from storage_analyzer.utils import (
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    classify_reparse_point,
    is_cloud_reparse_tag,
)


class FakeEntry:
    def __init__(self, path: str, *, symlink: bool = False) -> None:
        self.path = path
        self.name = path.rsplit("/", 1)[-1]
        self._symlink = symlink

    def is_symlink(self) -> bool:
        return self._symlink


def stat(**kwargs):
    return SimpleNamespace(**kwargs)


def test_normal_when_windows_attributes_are_missing() -> None:
    info = classify_reparse_point(stat(), FakeEntry("/tmp/normal"))
    assert info.kind == "not_reparse"
    assert not info.is_reparse


def test_symlink_is_classified_without_windows_attributes() -> None:
    info = classify_reparse_point(stat(), FakeEntry("/tmp/link", symlink=True))
    assert info.kind == "symlink"
    assert info.is_reparse


def test_symlink_tag_is_classified() -> None:
    info = classify_reparse_point(
        stat(st_file_attributes=0x400, st_reparse_tag=IO_REPARSE_TAG_SYMLINK),
        FakeEntry(r"C:/Users/me/link"),
    )
    assert info.kind == "symlink"


def test_mount_point_tag_is_classified_as_junction() -> None:
    info = classify_reparse_point(
        stat(st_file_attributes=0x400, st_reparse_tag=IO_REPARSE_TAG_MOUNT_POINT),
        FakeEntry(r"C:/Users/me/Application Data"),
    )
    assert info.kind == "junction"


def test_cloud_tag_with_onedrive_path_is_onedrive_cloud() -> None:
    assert is_cloud_reparse_tag(0x9000701A)
    info = classify_reparse_point(
        stat(st_file_attributes=0x400, st_reparse_tag=0x9000701A),
        FakeEntry(r"C:/Users/me/OneDrive/Documents"),
    )
    assert info.kind == "onedrive_cloud"
    assert info.is_cloud_tag
    assert info.is_onedrive_path


def test_cloud_tag_without_onedrive_path_is_not_traversable_onedrive() -> None:
    info = classify_reparse_point(
        stat(st_file_attributes=0x400, st_reparse_tag=0x9000701A),
        FakeEntry(r"C:/Users/me/CloudVendor/Documents"),
    )
    assert info.kind == "other_reparse"
    assert info.is_cloud_tag
    assert not info.is_onedrive_path


def test_unknown_reparse_without_tag_is_safe_unknown() -> None:
    info = classify_reparse_point(stat(st_file_attributes=0x400), FakeEntry(r"C:/unknown"))
    assert info.kind == "unknown_reparse"


def test_unknown_reparse_tag_is_other_reparse() -> None:
    info = classify_reparse_point(
        stat(st_file_attributes=0x400, st_reparse_tag=0xDEADBEEF),
        FakeEntry(r"C:/unknown"),
    )
    assert info.kind == "other_reparse"


def test_windows_normal_directory_attrs_with_zero_tag_are_not_reparse() -> None:
    for attrs in (16, 17, 19):
        info = classify_reparse_point(
            stat(st_file_attributes=attrs, st_reparse_tag=0),
            FakeEntry(r"C:/Users/normal"),
        )
        assert info.kind == "not_reparse"
        assert not info.is_reparse
        assert info.tag is None


def test_missing_windows_attributes_with_zero_tag_is_not_reparse() -> None:
    info = classify_reparse_point(stat(st_reparse_tag=0), FakeEntry(r"C:/Users/normal"))
    assert info.kind == "not_reparse"
    assert not info.is_reparse
