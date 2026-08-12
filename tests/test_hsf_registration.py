"""GSM 注册魔数（Owner/Signature）——Archicad 拒开事故的回归测试。

漏窗真机实测：OpenBrep 新建项目曾默认 Owner="0"/Signature="0"，
LP_XMLConverter 产出的 GSM 二进制头缺 "MYSGCASG" 注册魔数，
Archicad 29 报 "Unsupported document version or incorrect file" 拒绝打开。
魔数 = Signature("MYSG" LE=1196644685) + Owner("CASG" LE=1196638531)，
是 Graphisoft 常量而非逐文件值。

归一化放在编译期（compiler._normalize_registration）：modify 确定性路径
的变更守护只允许 paramlist.xml + scripts/*，加载/保存期改写 libpartdata
会触发守护回滚（M19 回放事故）。
"""

from openbrep.compiler import _normalize_registration
from openbrep.hsf_project import HSFProject


def test_new_project_defaults_to_graphisoft_registration():
    p = HSFProject.create_new("RegTest", "./workdir")
    assert p.owner == "1196638531"
    assert p.signature == "1196644685"


def test_libpartdata_written_with_registration(tmp_path):
    p = HSFProject.create_new("RegTest", str(tmp_path))
    hsf_dir = p.save_to_disk()
    content = (hsf_dir / "libpartdata.xml").read_text(encoding="utf-8-sig")
    assert 'Owner="1196638531"' in content
    assert 'Signature="1196644685"' in content


def _zero_registration(hsf_dir):
    lp = hsf_dir / "libpartdata.xml"
    content = lp.read_text(encoding="utf-8-sig")
    lp.write_text(
        content.replace('Owner="1196638531"', 'Owner="0"').replace(
            'Signature="1196644685"', 'Signature="0"'
        ),
        encoding="utf-8",
    )


def test_compile_normalizes_zero_registration(tmp_path):
    """旧项目（Owner/Signature 为 0）编译前被归一化为注册常量。"""
    p = HSFProject.create_new("RegHeal", str(tmp_path))
    hsf_dir = p.save_to_disk()
    _zero_registration(hsf_dir)

    assert _normalize_registration(hsf_dir) is True

    content = (hsf_dir / "libpartdata.xml").read_text(encoding="utf-8-sig")
    assert 'Owner="1196638531"' in content
    assert 'Signature="1196644685"' in content


def test_compile_normalization_preserves_real_registration(tmp_path):
    """反编译来的真实 Owner/Signature 不被改写；已归一化的也不重复改写。"""
    p = HSFProject.create_new("RegKeep", str(tmp_path))
    hsf_dir = p.save_to_disk()
    lp = hsf_dir / "libpartdata.xml"
    lp.write_text(
        lp.read_text(encoding="utf-8-sig").replace('Owner="1196638531"', 'Owner="1234567890"'),
        encoding="utf-8",
    )
    assert _normalize_registration(hsf_dir) is False
    assert 'Owner="1234567890"' in lp.read_text(encoding="utf-8-sig")

    # 已是常量的：幂等，返回 False
    _zero_registration(hsf_dir)
    assert _normalize_registration(hsf_dir) is True
    assert _normalize_registration(hsf_dir) is False
