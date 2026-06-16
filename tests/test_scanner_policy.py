from types import SimpleNamespace

from storage_analyzer.config import Config
from storage_analyzer.scanner import ScanStats, Scanner
from storage_analyzer.utils import IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK


class FakeDirEntry:
    def __init__(self, path: str, *, attrs: int = 0, tag=None, symlink: bool = False) -> None:
        self.path = path
        self.name = path.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        self._attrs = attrs
        self._tag = tag
        self._symlink = symlink

    def is_symlink(self) -> bool:
        return self._symlink

    def stat(self, follow_symlinks: bool = False):
        values = {"st_file_attributes": self._attrs, "st_ino": id(self), "st_dev": 1}
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
