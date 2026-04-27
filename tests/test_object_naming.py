from ui.object_naming import extract_object_name


def test_extract_object_name_prefers_explicit_name():
    assert extract_object_name("创建一个 named CustomShelf 的书架") == "CustomShelf"


def test_extract_object_name_matches_longest_chinese_keyword():
    assert extract_object_name("创建一个推拉门") == "SlidingDoor"


def test_extract_object_name_falls_back_to_camel_case_word():
    assert extract_object_name("make ParametricCabinet with drawers") == "ParametricCabinet"
