from openbrep.ui_layout import parse_ui_layout


SOFA_UI = """
! v2 UI Script
UI_PAGE 1

UI_INFIELD{2} A, 110, 20, 90, 20
UI_INFIELD{2} B, 110, 45, 90, 20
UI_INFIELD{2} seat_h, 110, 140, 90, 20
UI_INFIELD{2} has_glide, 365, 20, 90, 20
"""

STAIR_FRAGMENT = """
ui_dialog `2D符号`,444,266
pos_x=0
pos_y=38

IF gs_UIPages=`2D 符号类型` THEN
	ui_page 1
	UI_OUTFIELD `2D细节级别`,	4,	34,138,20
	ui_infield "gs_detlevel_2D",	144, 30,144,20
ENDIF

IF gs_UIPages=`左围栏类型` THEN
	ui_page 2
	ui_infield "rtyp",0,38,444,228,
	1,`竖直栏杆`,
	2,`水平栏杆`
ENDIF
"""


def test_parse_simple_ui_infield_layout():
    result = parse_ui_layout(SOFA_UI, parameters={"A": 1.2, "B": 0.9, "seat_h": 0.4, "has_glide": 1})
    assert result.ok
    assert result.active_page == 1
    infields = [c for c in result.controls if c.type == "infield"]
    assert [c.param for c in infields] == ["A", "B", "seat_h", "has_glide"]
    a = infields[0]
    assert (a.x, a.y, a.w, a.h) == (110, 20, 90, 20)


def test_if_branch_selected_by_parameter():
    result = parse_ui_layout(
        STAIR_FRAGMENT,
        parameters={"gs_UIPages": "2D 符号类型", "gs_detlevel_2D": "自定义", "rtyp": "竖直栏杆"},
    )
    params = [c.param for c in result.controls if c.type == "infield"]
    assert "gs_detlevel_2D" in params
    assert "rtyp" not in params
    assert result.title == "2D符号"
    assert result.width >= 444

    other = parse_ui_layout(
        STAIR_FRAGMENT,
        parameters={"gs_UIPages": "左围栏类型", "gs_detlevel_2D": "自定义", "rtyp": "竖直栏杆"},
    )
    params2 = [c.param for c in other.controls if c.type == "infield"]
    assert "rtyp" in params2
    assert "gs_detlevel_2D" not in params2
    rtyp = next(c for c in other.controls if c.param == "rtyp")
    assert rtyp.options == [
        {"value": 1, "label": "竖直栏杆"},
        {"value": 2, "label": "水平栏杆"},
    ]


def test_complex_picture_infield_degrades():
    script = """
UI_INFIELD "gs_SymbolType",0,0,444,228,
1,"ui_Stair2DSymbolTypes_11",20,3,
60,194,51,168,
1,`类型1`
"""
    result = parse_ui_layout(script, parameters={"gs_SymbolType": 1})
    assert not any(c.param == "gs_SymbolType" for c in result.controls if c.type == "infield")
    assert any("复杂" in item for item in result.unsupported)


def test_empty_or_comment_only_script():
    result = parse_ui_layout("! only comments\n")
    assert result.ok
    assert result.controls == []
    assert result.to_dict()["has_infield"] is False


def test_outfield_and_separator_geometry():
    script = """
UI_DIALOG "样本",500,300
UI_OUTFIELD "说明文字",20,30,400,16
UI_SEPARATOR 10,20,430,20
"""
    result = parse_ui_layout(script)
    assert result.title == "样本"
    assert result.width == 500
    outfield = next(c for c in result.controls if c.type == "outfield")
    assert outfield.text == "说明文字"
    assert outfield.x == 20
    sep = next(c for c in result.controls if c.type == "separator")
    assert sep.w > 0


def test_values_declaration_provides_options():
    script = 'UI_INFIELD "rail", 10, 10, 80, 20\n'
    result = parse_ui_layout(
        script,
        parameters={"rail": "双方"},
        values_declarations={"rail": {"options": ["双方", "左", "右"]}},
    )
    infield = next(c for c in result.controls if c.type == "infield")
    assert infield.options == ["双方", "左", "右"]
