from types import SimpleNamespace

import pytest

from storage_analyzer.config import Config
from storage_analyzer.scanner import ScanStats, Scanner
from storage_analyzer.utils import IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK, classify_reparse_point


class FakeDirEntry:
    def __init__(
        self,
        path: str,
        *,
        attrs: int = 0,
        tag=None,
        symlink: bool = False,
        is_dir: bool = True,
        size: int = 123,
    ) -> None:
        self.path = path
        self.name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        self._attrs = attrs
        self._tag = tag
        self._symlink = symlink
        self._is_dir = is_dir
        self._size = size

    def is_dir(self, follow_symlinks: bool = False) -> bool:
        return self._is_dir

    def is_symlink(self) -> bool:
        return self._symlink

    def stat(self, follow_symlinks: bool = False):
        values = {
            "st_file_attributes": self._attrs,
            "st_ino": id(self),
            "st_dev": 1,
            "st_size": self._size,
            "st_mtime": 0,
            "st_ctime": 0,
        }
        if self._tag is not None:
            values["st_reparse_tag"] = self._tag
        return SimpleNamespace(**values)


def make_scanner(cfg: Config | None = None) -> Scanner:
    return Scanner(cfg or Config(), lambda skip: None, ScanStats())


def test_normal_directory_descends() -> None:
    scanner = make_scanner()
    assert scanner._should_descend(FakeDirEntry("/tmp/normal"), False)


def test_symlink_is_skipped_by_default() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(r"C:/Users/me/link", attrs=0x400, tag=IO_REPARSE_TAG_SYMLINK, symlink=True)
    assert not scanner._should_descend(entry, False)
    assert scanner.stats.symlink_skipped == 1


def test_junction_is_skipped_by_default() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(r"C:/Users/me/Application Data", attrs=0x400, tag=IO_REPARSE_TAG_MOUNT_POINT)
    assert not scanner._should_descend(entry, False)
    assert scanner.stats.junction_skipped == 1


def test_onedrive_cloud_descends_when_enabled() -> None:
    scanner = make_scanner(Config(traverse_onedrive_cloud_reparse=True))
    entry = FakeDirEntry(r"C:/Users/me/OneDrive/Documents", attrs=0x400, tag=0x9000701A)
    assert scanner._should_descend(entry, False)
    assert scanner.stats.onedrive_cloud_reparse_detected == 1
    assert scanner.stats.onedrive_cloud_reparse_descended == 1


def test_onedrive_cloud_skips_when_disabled() -> None:
    scanner = make_scanner(Config(traverse_onedrive_cloud_reparse=False))
    entry = FakeDirEntry(r"C:/Users/me/OneDrive/Documents", attrs=0x400, tag=0x9000701A)
    assert not scanner._should_descend(entry, False)
    assert scanner.stats.onedrive_cloud_reparse_detected == 1
    assert scanner.stats.onedrive_cloud_reparse_skipped == 1


def test_unknown_reparse_is_skipped() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(r"C:/Users/me/unknown", attrs=0x400)
    assert not scanner._should_descend(entry, False)
    assert scanner.stats.unknown_reparse_skipped == 1


def test_excluded_directory_is_skipped() -> None:
    cfg = Config(exclude_dir_names=("skipme",))
    scanner = make_scanner(cfg)
    assert not scanner._should_descend(FakeDirEntry("/tmp/skipme"), False)


def test_precount_and_scan_policy_match_for_reparse_kinds() -> None:
    entries = [
        FakeDirEntry("/tmp/normal"),
        FakeDirEntry(r"C:/Users/me/link", attrs=0x400, tag=IO_REPARSE_TAG_SYMLINK, symlink=True),
        FakeDirEntry(r"C:/Users/me/Application Data", attrs=0x400, tag=IO_REPARSE_TAG_MOUNT_POINT),
        FakeDirEntry(r"C:/Users/me/OneDrive/Documents", attrs=0x400, tag=0x9000701A),
        FakeDirEntry(r"C:/Users/me/unknown", attrs=0x400),
    ]
    for entry in entries:
        cfg = Config()
        assert make_scanner(cfg)._should_descend(entry, False, record=False) == make_scanner(cfg)._should_descend(entry, False, record=True)


def test_reparse_record_limit_is_honored() -> None:
    stats = ScanStats()
    for i in range(5):
        stats.add_reparse_record({"path": str(i)}, limit=2)
    assert stats.reparse_summary()["records"] == [{"path": "0"}, {"path": "1"}]


def test_cloud_reparse_file_is_recorded_as_metadata_only() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(r"C:/Users/me/OneDrive/file.txt", attrs=0x400, tag=0x9000701A)
    info = classify_reparse_point(entry.stat(False), entry)
    scanner._record_reparse_file(entry.path, info)
    summary = scanner.stats.reparse_summary()
    assert summary["onedrive_cloud_file_detected"] == 1
    assert summary["records"][0]["action"] == "file_metadata"


def test_windows_normal_directory_attrs_with_zero_tag_descends() -> None:
    scanner = make_scanner()
    for attrs in (16, 17, 19):
        assert scanner._should_descend(FakeDirEntry(rf"C:/Users/normal-{attrs}", attrs=attrs, tag=0), False)


def test_c_users_like_tree_descends_into_normal_directories(tmp_path) -> None:
    root = tmp_path / "root"
    user1 = root / "user1"
    public = root / "Public"
    user1.mkdir(parents=True)
    public.mkdir()
    all_users_target = tmp_path / "all_users_target"
    all_users_target.mkdir()
    (all_users_target / "ignored.txt").write_text("ignored", encoding="utf-8")
    try:
        (root / "All Users").symlink_to(all_users_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"この環境ではディレクトリ symlink を作成できない: {exc}")
    (user1 / "a.txt").write_text("a", encoding="utf-8")
    (public / "b.txt").write_text("b", encoding="utf-8")
    (root / "desktop.ini").write_text("ini", encoding="utf-8")

    scanner = make_scanner()
    precount = scanner.count_files(str(root))
    records = list(scanner.iter_records(str(root)))

    assert precount == 3
    assert {record.name for record in records} == {"a.txt", "b.txt", "desktop.ini"}
    assert "ignored.txt" not in {record.name for record in records}
    assert scanner.stats.file_count == 3
    assert scanner.stats.folder_count >= 2


def process_file(scanner: Scanner, entry: FakeDirEntry):
    return scanner._process_entry(entry, "/parent", 1, [], False)


def test_symlink_directory_that_is_not_reported_as_dir_is_not_counted_as_file() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(
        r"C:/Users/All Users", attrs=0x400, tag=IO_REPARSE_TAG_SYMLINK, symlink=True, is_dir=False
    )

    assert process_file(scanner, entry) is None
    assert scanner.stats.file_count == 0
    assert scanner.stats.total_bytes == 0
    assert scanner.stats.other_reparse_file_detected == 1


def test_symlink_file_is_not_counted_as_regular_file_when_not_following() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(
        r"C:/Users/me/link.txt", attrs=0x400, tag=IO_REPARSE_TAG_SYMLINK, symlink=True, is_dir=False
    )

    assert process_file(scanner, entry) is None
    assert scanner.stats.file_count == 0
    assert scanner.stats.total_bytes == 0


def test_junction_like_file_branch_entry_is_not_counted_as_regular_file() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(
        r"C:/Users/Default User", attrs=0x400, tag=IO_REPARSE_TAG_MOUNT_POINT, is_dir=False
    )

    assert process_file(scanner, entry) is None
    assert scanner.stats.file_count == 0
    assert scanner.stats.total_bytes == 0


def test_unknown_reparse_file_is_not_counted_as_regular_file() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(r"C:/Users/me/unknown", attrs=0x400, is_dir=False)

    assert process_file(scanner, entry) is None
    assert scanner.stats.file_count == 0
    assert scanner.stats.total_bytes == 0


def test_onedrive_cloud_file_is_counted_from_metadata_only() -> None:
    scanner = make_scanner()
    entry = FakeDirEntry(
        r"C:/Users/me/OneDrive/cloud.txt", attrs=0x400, tag=0x9000701A, is_dir=False, size=456
    )

    rec = process_file(scanner, entry)

    assert rec is not None
    assert rec.size_bytes == 456
    assert scanner.stats.file_count == 1
    assert scanner.stats.total_bytes == 456
    assert scanner.stats.onedrive_cloud_file_detected == 1
