from storage_analyzer.palette import get_category_color, get_label_color, make_color_sequence


def test_category_color_is_stable_for_known_category() -> None:
    assert get_category_color("Video") == get_category_color("Video")


def test_unknown_category_returns_stable_color() -> None:
    first = get_category_color("UnknownCustomCategory")
    second = get_category_color("UnknownCustomCategory")
    assert first == second
    assert first.startswith("#")


def test_label_color_uses_stable_hash_not_runtime_hash() -> None:
    assert get_label_color("Documents") == get_label_color("Documents")
    assert get_label_color("Documents").startswith("#")


def test_make_color_sequence_matches_label_count() -> None:
    labels = ["a", "b", "c", "a"]
    colors = make_color_sequence(labels)
    assert len(colors) == len(labels)
    assert colors[0] == colors[3]
