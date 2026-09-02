## 2026-07-29T03:21:25 · MODIFY / r0003

- Instruction: 踏步有问题，看看是脚本问题，还是没有写踏步的脚本
- Changed files: scripts/1d.gdl, scripts/3d.gdl

**变更摘要：**
- 修改脚本：scripts/1d.gdl, scripts/3d.gdl
- 未改脚本：scripts/2d.gdl, scripts/vl.gdl, scripts/ui.gdl, scripts/pr.gdl
- 编译结果：✅ 通过

## 2026-07-29T03:59:29 · MODIFY / r0005

- Instruction: 脚本检查提示如下，检查修复：3d脚本：Missing CALL keyword (not recommended)
at line 73 in the 3D script of file gdl_2.gsm.；Parameter 'RISER_HEIGHT' overwrites global value
at line 0 in the Master script of file gdl_2.gsm.2d脚本：Parameter 'RISER_HEIGHT' overwrites global value
at line 0 in the Master script of file gdl_2.gsm.The GDL script contains minor problems.
- Changed files: paramlist.xml, scripts/1d.gdl, scripts/3d.gdl, scripts/vl.gdl

**变更摘要：**
- 修改脚本：paramlist.xml, scripts/1d.gdl, scripts/3d.gdl, scripts/vl.gdl
- 未改脚本：scripts/2d.gdl, scripts/ui.gdl, scripts/pr.gdl
- 新增参数：step_riser（Length，默认 0.17）
- 删除参数：riser_height
- 编译结果：✅ 通过
