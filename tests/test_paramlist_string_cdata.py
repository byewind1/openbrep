"""String 参数 paramlist.xml CDATA 包裹（LP_XMLConverter 真机实测规则）。

漏窗真机编译暴露：LP_XMLConverter 要求 String 参数的 Value 必须
CDATA 包裹且带引号（<Value><![CDATA["文本"]]></Value>）：
- 裸文本 <Value>直棂</Value> → error: Missing CDATA section in tag 'Value'
- 无引号 CDATA <Value><![CDATA[直棂]]></Value> → error: String value error
"""

from openbrep.hsf_project import GDLParameter
from openbrep.paramlist_builder import build_paramlist_xml, parse_paramlist_xml


def _roundtrip(params):
    return parse_paramlist_xml(build_paramlist_xml(params))


def test_string_value_written_as_quoted_cdata():
    xml = build_paramlist_xml([GDLParameter(name="pattern_type", type_tag="String", value="直棂")])
    assert '<Value><![CDATA["直棂"]]></Value>' in xml


def test_string_value_roundtrip_cjk():
    params = [GDLParameter(name="pattern_type", type_tag="String", value="直棂", description="纹样")]
    (parsed,) = _roundtrip(params)
    assert parsed.value == "直棂"
    assert parsed.type_tag == "String"


def test_string_value_roundtrip_empty():
    (parsed,) = _roundtrip([GDLParameter(name="note", type_tag="String", value="")])
    assert parsed.value == ""


def test_parse_legacy_plain_string_value():
    """旧格式（裸文本 Value，无 CDATA）读回不受影响。"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<ParamSection>\n\t<Parameters>\n'
        '\t\t<String Name="pattern_type">\n'
        '\t\t\t<Description><![CDATA["纹样"]]></Description>\n'
        '\t\t\t<Value>直棂</Value>\n'
        '\t\t</String>\n\t</Parameters>\n</ParamSection>\n'
    )
    (parsed,) = parse_paramlist_xml(xml)
    assert parsed.value == "直棂"


def test_numeric_and_material_values_not_cdata():
    xml = build_paramlist_xml([
        GDLParameter(name="A", type_tag="Length", value=0.9),
        GDLParameter(name="mat_frame", type_tag="Material", value=0),
    ])
    assert "<Value>0.9</Value>" in xml
    assert "<Value>0</Value>" in xml
    assert "<Value><![CDATA[" not in xml  # 非 String 类型不包 CDATA
