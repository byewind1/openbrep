# OpenBrep 代码健康度报告
生成时间：2026-03-16 22:50:46

> 注意：这是 2026-03-16 的历史健康度快照，保留用于对比早期技术债。
> 当前架构与行数请以 `docs/ARCHITECTURE.zh-CN.md` 和 `docs/AI_DEVELOPMENT_GUIDE.zh-CN.md` 为准。
> 截至 2026-04-27，`ui/app.py` 已从本报告中的 3389 行治理到 1812 行，并拆出
> `project_service`、`generation_service`、`app_shell`、`chat_controller`、`views/*`
> 等边界。

## 概览
| 指标 | 数值 |
|------|------|
| Python 文件总数 | 28 |
| 总代码行数 | 11383 |
| 平均函数长度 | 21.2 |
| ⚠️ 超长函数数量 | 31 |
| ⚠️ 深度嵌套数量 | 47 |
| 测试覆盖率 | 0%（无测试） |

**Python 文件清单（含行数/函数/类）**
| 文件 | 行数 | 函数数 | 类数 |
|------|----:|------:|----:|
| openbrep/__init__.py | 7 | 0 | 0 |
| openbrep/cli.py | 471 | 13 | 0 |
| openbrep/compiler.py | 320 | 11 | 3 |
| openbrep/config.py | 382 | 13 | 4 |
| openbrep/context.py | 196 | 5 | 1 |
| openbrep/core.py | 692 | 9 | 3 |
| openbrep/cross_script_checker.py | 40 | 1 | 1 |
| openbrep/dependencies.py | 201 | 9 | 2 |
| openbrep/gdl_parser.py | 320 | 10 | 0 |
| openbrep/gdl_previewer.py | 894 | 32 | 5 |
| openbrep/hsf_project.py | 443 | 21 | 3 |
| openbrep/knowledge.py | 305 | 9 | 1 |
| openbrep/llm.py | 297 | 7 | 4 |
| openbrep/paramlist_builder.py | 268 | 5 | 0 |
| openbrep/preflight.py | 160 | 4 | 2 |
| openbrep/sandbox.py | 144 | 10 | 2 |
| openbrep/skills_loader.py | 152 | 7 | 1 |
| openbrep/snippets.py | 352 | 6 | 2 |
| openbrep/tapir_bridge.py | 635 | 27 | 2 |
| openbrep/validator.py | 161 | 7 | 2 |
| openbrep/xml_utils.py | 258 | 9 | 1 |
| packaging/openbrep_launcher.py | 42 | 2 | 0 |
| run_tests.py | 822 | 64 | 2 |
| scripts/gen_keys.py | 43 | 1 | 0 |
| scripts/gen_license.py | 92 | 4 | 0 |
| scripts/pack_pro.py | 90 | 2 | 0 |
| scripts/ui_preview_smoke.py | 207 | 4 | 0 |
| ui/app.py | 3389 | 71 | 0 |

**最长文件 Top 5**
| 文件 | 行数 |
|------|----:|
| /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/ui/app.py | 3389 |
| /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/openbrep/gdl_previewer.py | 894 |
| /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/run_tests.py | 822 |
| /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/openbrep/core.py | 692 |
| /Users/ren/MAC工作/工作/code/开源项目/gdl-agent/openbrep/tapir_bridge.py | 635 |

## 核心文件分析
### openbrep/core.py
- 总行数：692
- 函数数量：9，类数量：3

**函数列表（含行数）**
| 函数 | 行数 | 位置 |
|---|---:|---|
| __init__ | 13 | 53-65 |
| run ⚠️ | 139 | 67-205 |
| generate_only ⚠️ | 168 | 207-374 |
| _build_context | 49 | 378-426 |
| _build_messages ⚠️ | 72 | 428-499 |
| _build_system_prompt ⚠️ | 92 | 501-592 |
| _parse_response | 40 | 596-635 |
| _apply_changes | 15 | 637-651 |
| _parse_param_text | 34 | 653-686 |

**嵌套深度检查（>4）**
- ⚠️ 深度嵌套位置：
  - generate_only @ L341 (depth=5, If)
  - generate_only @ L355 (depth=6, If)

**if/elif 长链检查（>5）**
无

### openbrep/llm.py
- 总行数：297
- 函数数量：7，类数量：4

**函数列表（含行数）**
| 函数 | 行数 | 位置 |
|---|---:|---|
| __init__ | 4 | 39-42 |
| _setup | 31 | 44-74 |
| generate ⚠️ | 73 | 76-148 |
| generate_with_image ⚠️ | 87 | 150-236 |
| _resolve_model_string | 35 | 238-272 |
| __init__ | 4 | 282-285 |
| generate | 11 | 287-297 |

**嵌套深度检查（>4）**
- ⚠️ 深度嵌套位置：
  - _setup @ L59 (depth=5, If)
  - _setup @ L61 (depth=6, If)
  - _resolve_model_string @ L263 (depth=5, If)
  - _resolve_model_string @ L265 (depth=6, If)

**if/elif 长链检查（>5）**
- ⚠️ if/elif 链超过 5 分支：
  - L254 (length=6)

### openbrep/config.py
- 总行数：382
- 函数数量：13，类数量：4

**函数列表（含行数）**
| 函数 | 行数 | 位置 |
|---|---:|---|
| model_to_provider | 19 | 101-119 |
| _auto_detect_converter | 14 | 122-135 |
| resolve_api_key | 41 | 148-188 |
| resolve_api_base | 16 | 190-205 |
| get_provider_for_model | 9 | 207-215 |
| load | 49 | 244-292 |
| _from_dict | 23 | 295-317 |
| pick | 2 | 296-297 |
| get_available_models | 7 | 319-325 |
| ensure_dirs | 3 | 327-329 |
| save | 18 | 331-348 |
| to_toml_string | 26 | 350-375 |
| _nested_set | 5 | 378-382 |

**嵌套深度检查（>4）**
- ⚠️ 深度嵌套位置：
  - resolve_api_key @ L168 (depth=5, If)
  - resolve_api_key @ L171 (depth=5, For)
  - resolve_api_key @ L172 (depth=6, If)
  - load @ L272 (depth=5, If)
  - load @ L274 (depth=6, If)
  - load @ L277 (depth=6, If)
  - load @ L279 (depth=6, If)
  - load @ L281 (depth=7, If)

**if/elif 长链检查（>5）**
无

### ui/app.py
- 总行数：3389
- 函数数量：71，类数量：0

**函数列表（含行数）**
| 函数 | 行数 | 位置 |
|---|---:|---|
| _reset_tapir_p0_state | 12 | 241-252 |
| _license_file | 2 | 255-256 |
| _load_license | 8 | 259-266 |
| _save_license | 4 | 269-272 |
| _to_base36 | 10 | 275-284 |
| _get_license_secret | 10 | 287-296 |
| _gen_code | 5 | 299-303 |
| _verify_pro_code ⚠️ | 63 | 306-368 |
| _import_pro_knowledge_zip | 38 | 371-408 |
| _key_for_model | 21 | 445-465 |
| _is_archicad_running | 9 | 468-476 |
| _model_label | 5 | 572-576 |
| _get_default_api_base | 14 | 653-666 |
| _save_feedback | 16 | 708-723 |
| _tapir_sync_selection | 25 | 726-750 |
| _tapir_highlight_selection | 21 | 753-773 |
| _tapir_load_selected_params ⚠️ | 81 | 776-856 |
| _tapir_apply_param_edits ⚠️ | 96 | 859-954 |
| _render_tapir_inspector_panel | 24 | 957-980 |
| _render_tapir_param_workbench_panel | 31 | 983-1013 |
| _fullscreen_editor_dialog | 24 | 1021-1044 |
| _fullscreen_editor_dialog | 2 | 1046-1047 |
| get_compiler | 4 | 1050-1053 |
| get_llm | 11 | 1055-1065 |
| load_knowledge | 22 | 1067-1088 |
| load_skills | 14 | 1090-1103 |
| _versioned_gsm_path | 16 | 1105-1120 |
| _max_existing_gsm_revision | 17 | 1123-1139 |
| _safe_compile_revision | 4 | 1142-1145 |
| _derive_gsm_name_from_filename | 21 | 1148-1168 |
| _extract_gsm_name_candidate | 19 | 1171-1189 |
| _stamp_script_header | 11 | 1192-1202 |
| _extract_object_name | 29 | 1228-1256 |
| show_welcome | 32 | 1261-1292 |
| _is_gdl_intent | 3 | 1322-1324 |
| _is_pure_chat | 2 | 1326-1327 |
| classify_and_extract | 42 | 1329-1370 |
| chat_respond | 23 | 1373-1395 |
| _main_editor_state_key | 3 | 1409-1411 |
| _mark_main_ace_editors_pending | 8 | 1415-1422 |
| _bump_main_editor_version | 4 | 1425-1428 |
| _is_debug_intent | 8 | 1448-1455 |
| _get_debug_mode | 5 | 1457-1461 |
| run_agent_generate ⚠️ | 140 | 1464-1603 |
| on_event | 21 | 1486-1506 |
| _parse_paramlist_text | 24 | 1606-1629 |
| _sanitize_script_content | 38 | 1632-1669 |
| _apply_scripts_to_project | 47 | 1672-1718 |
| do_compile | 32 | 1721-1752 |
| import_gsm ⚠️ | 99 | 1755-1853 |
| _find_hsf_root | 18 | 1802-1819 |
| _handle_unified_import | 40 | 1856-1895 |
| _strip_md_fences | 8 | 1898-1905 |
| _classify_code_blocks | 39 | 1908-1946 |
| _extract_gdl_from_text | 3 | 1949-1951 |
| _extract_gdl_from_chat | 9 | 1954-1962 |
| _build_chat_script_anchors | 29 | 1965-1993 |
| _thumb_image_bytes | 7 | 1996-2002 |
| _detect_image_task_mode | 28 | 2005-2032 |
| run_vision_generate ⚠️ | 60 | 2082-2141 |
| check_gdl_script ⚠️ | 102 | 2144-2245 |
| _to_float | 13 | 2248-2260 |
| _preview_param_values | 18 | 2263-2280 |
| _dedupe_keep_order | 9 | 2283-2291 |
| _collect_preview_prechecks | 24 | 2294-2317 |
| _sync_visible_editor_buffers | 29 | 2320-2348 |
| _render_preview_2d ⚠️ | 79 | 2351-2429 |
| _render_preview_3d ⚠️ | 55 | 2432-2486 |
| _run_preview | 30 | 2489-2518 |
| _show_log_dialog | 13 | 2706-2718 |
| _adopt_confirm_dialog | 22 | 3012-3033 |

**嵌套深度检查（>4）**
- ⚠️ 深度嵌套位置：
  - _verify_pro_code @ L329 (depth=5, If)
  - _verify_pro_code @ L358 (depth=5, Try)
  - _verify_pro_code @ L359 (depth=6, If)
  - _key_for_model @ L463 (depth=5, If)
  - _tapir_apply_param_edits @ L899 (depth=5, If)
  - _tapir_apply_param_edits @ L905 (depth=5, Try)
  - _tapir_apply_param_edits @ L910 (depth=5, If)
  - _tapir_apply_param_edits @ L911 (depth=6, Try)
  - run_agent_generate @ L1498 (depth=5, If)
  - run_agent_generate @ L1500 (depth=6, If)
  - run_agent_generate @ L1504 (depth=5, If)
  - _classify_code_blocks @ L1939 (depth=5, If)
  - _classify_code_blocks @ L1941 (depth=6, If)
  - check_gdl_script @ L2206 (depth=5, If)
  - check_gdl_script @ L2208 (depth=6, If)
  - on_event @ L1498 (depth=5, If)
  - on_event @ L1500 (depth=6, If)
  - on_event @ L1504 (depth=5, If)

**if/elif 长链检查（>5）**
- ⚠️ if/elif 链超过 5 分支：
  - L637 (length=6)

## 耦合热点
**被依赖最多的模块（Top 10）**
| 模块 | 依赖模块数 |
|------|-----------:|
| openbrep | 12 |
| openbrep.hsf_project | 8 |
| openbrep.paramlist_builder | 5 |
| openbrep.compiler | 4 |
| openbrep.core | 3 |
| openbrep.validator | 3 |
| openbrep.config | 3 |
| openbrep.gdl_parser | 2 |
| openbrep.gdl_previewer | 2 |
| openbrep.skills_loader | 2 |

**ui/app.py 中直接调用 openbrep/ 模块的位置**
- L40: from openbrep.hsf_project import HSFProject, ScriptType, GDLParameter
- L41: from openbrep.gdl_parser import parse_gdl_source, parse_gdl_file
- L42: from openbrep.paramlist_builder import build_paramlist_xml, validate_paramlist
- L43: from openbrep.compiler import MockHSFCompiler, HSFCompiler, CompileResult
- L44: from openbrep.core import GDLAgent, Status
- L45: from openbrep.gdl_previewer import Preview2DResult, Preview3DResult, preview_2d_script, preview_3d_script
- L46: from openbrep.validator import GDLValidator
- L47: from openbrep.knowledge import KnowledgeBase
- L49:     from openbrep.config import ALL_MODELS, VISION_MODELS, REASONING_MODELS, model_to_provider
- L56: from openbrep.skills_loader import SkillsLoader
- L57: from openbrep import __version__ as OPENBREP_VERSION
- L59:     from openbrep.tapir_bridge import get_bridge, errors_to_chat_message
- L418:     from openbrep.config import GDLAgentConfig
- L616:             from openbrep.config import GDLAgentConfig, model_to_provider
- L629:             from openbrep.config import GDLAgentConfig
- L1056:     from openbrep.config import LLMConfig
- L1057:     from openbrep.llm import LLMAdapter

## session_state 读写地图
- L156: if "project" not in st.session_state:
- L157:     st.session_state.project = None
- L158: if "_import_key_done" not in st.session_state:
- L159:     st.session_state._import_key_done = ""   # dedup: skip re-processing same file
- L160: if "compile_log" not in st.session_state:
- L161:     st.session_state.compile_log = []
- L162: if "compile_result" not in st.session_state:
- L163:     st.session_state.compile_result = None
- L164: if "tapir_status" not in st.session_state:
- L165:     st.session_state.tapir_status = None  # None | "checking" | "ok" | "no_tapir" | "no_ac"
- L166: if "tapir_test_trigger" not in st.session_state:
- L167:     st.session_state.tapir_test_trigger = False
- L168: if "tapir_selection_trigger" not in st.session_state:
- L169:     st.session_state.tapir_selection_trigger = False
- L170: if "tapir_highlight_trigger" not in st.session_state:
- L171:     st.session_state.tapir_highlight_trigger = False
- L172: if "tapir_load_params_trigger" not in st.session_state:
- L173:     st.session_state.tapir_load_params_trigger = False
- L174: if "tapir_apply_params_trigger" not in st.session_state:
- L175:     st.session_state.tapir_apply_params_trigger = False
- L176: if "tapir_selected_guids" not in st.session_state:
- L177:     st.session_state.tapir_selected_guids = []
- L178: if "tapir_selected_details" not in st.session_state:
- L179:     st.session_state.tapir_selected_details = []
- L180: if "tapir_selected_params" not in st.session_state:
- L181:     st.session_state.tapir_selected_params = []
- L182: if "tapir_param_edits" not in st.session_state:
- L183:     st.session_state.tapir_param_edits = {}
- L184: if "tapir_last_error" not in st.session_state:
- L185:     st.session_state.tapir_last_error = ""
- L186: if "tapir_last_sync_at" not in st.session_state:
- L187:     st.session_state.tapir_last_sync_at = ""
- L188: if "adopted_msg_index" not in st.session_state:
- L189:     st.session_state.adopted_msg_index = None
- L190: if "_debug_mode_active" not in st.session_state:
- L191:     st.session_state["_debug_mode_active"] = None  # None | "editor"
- L192: if "chat_history" not in st.session_state:
- L193:     st.session_state.chat_history = []
- L194: if "work_dir" not in st.session_state:
- L195:     st.session_state.work_dir = str(Path.home() / "openbrep-workspace")
- L196: if "agent_running" not in st.session_state:
- L197:     st.session_state.agent_running = False
- L198: if "pending_diffs" not in st.session_state:
- L201:     st.session_state.pending_diffs = {}
- L202: if "pending_ai_label" not in st.session_state:
- L204:     st.session_state.pending_ai_label = ""
- L205: if "pending_gsm_name" not in st.session_state:
- L206:     st.session_state.pending_gsm_name = ""
- L207: if "confirm_clear" not in st.session_state:
- L208:     st.session_state.confirm_clear = False
- L209: if "editor_version" not in st.session_state:
- L211:     st.session_state.editor_version = 0
- L212: if "_ace_pending_main_editor_keys" not in st.session_state:
- L213:     st.session_state._ace_pending_main_editor_keys = set()
- L214: if "script_revision" not in st.session_state:
- L216:     st.session_state.script_revision = 0
- L217: if "model_api_keys" not in st.session_state:
- L219:     st.session_state.model_api_keys = {}
- L220: if "chat_image_route_mode" not in st.session_state:
- L222:     st.session_state.chat_image_route_mode = "自动"
- L223: if "chat_anchor_focus" not in st.session_state:
- L224:     st.session_state.chat_anchor_focus = None
- L225: if "chat_anchor_pending" not in st.session_state:
- L226:     st.session_state.chat_anchor_pending = None
- L227: if "pro_unlocked" not in st.session_state:
- L228:     st.session_state.pro_unlocked = False
- L229: if "pro_license_loaded" not in st.session_state:
- L230:     st.session_state.pro_license_loaded = False
- L231: if "preview_2d_data" not in st.session_state:
- L232:     st.session_state.preview_2d_data = None
- L233: if "preview_3d_data" not in st.session_state:
- L234:     st.session_state.preview_3d_data = None
- L235: if "preview_warnings" not in st.session_state:
- L236:     st.session_state.preview_warnings = []
- L237: if "preview_meta" not in st.session_state:
- L238:     st.session_state.preview_meta = {"kind": "", "timestamp": ""}
- L243:     st.session_state.tapir_selection_trigger = False
- L244:     st.session_state.tapir_highlight_trigger = False
- L245:     st.session_state.tapir_load_params_trigger = False
- L246:     st.session_state.tapir_apply_params_trigger = False
- L247:     st.session_state.tapir_selected_guids = []
- L248:     st.session_state.tapir_selected_details = []
- L249:     st.session_state.tapir_selected_params = []
- L250:     st.session_state.tapir_param_edits = {}
- L251:     st.session_state.tapir_last_error = ""
- L252:     st.session_state.tapir_last_sync_at = ""
- L491:     work_dir = st.text_input("Work Directory", value=st.session_state.work_dir, label_visibility="collapsed", disabled=st.session_state.agent_running)
- L492:     st.session_state.work_dir = work_dir
- L495:     if not st.session_state.pro_license_loaded:
- L497:         st.session_state.pro_unlocked = bool(_lic.get("pro_unlocked", False))
- L498:         st.session_state.pro_license_loaded = True
- L501:     if st.session_state.pro_unlocked:
- L512:                 st.session_state.pro_unlocked = True
- L524:             st.session_state.pro_unlocked = False
- L529:     if st.session_state.pro_unlocked:
- L583:     _selected_label = st.selectbox("模型 / Model", _mo_labels, index=default_index, disabled=st.session_state.agent_running)
- L586:     st.session_state["current_model"] = model_name  # 供视觉检测使用
- L589:     if model_name not in st.session_state.model_api_keys:
- L591:         st.session_state.model_api_keys[model_name] = _key_for_model(model_name)
- L601:         api_key = st.session_state.model_api_keys.get(model_name, "")
- L605:             value=st.session_state.model_api_keys.get(model_name, ""),
- L608:             disabled=st.session_state.agent_running,
- L612:     if api_key != st.session_state.model_api_keys.get(model_name, ""):
- L613:         st.session_state.model_api_keys[model_name] = api_key
- L678:     if st.session_state.project:
- L680:             _keep_work_dir  = st.session_state.work_dir
- L681:             _keep_api_keys  = st.session_state.model_api_keys
- L682:             _keep_chat      = st.session_state.chat_history   # preserve chat
- L683:             st.session_state.project          = None
- L684:             st.session_state.compile_log      = []
- L685:             st.session_state.compile_result   = None
- L686:             st.session_state.adopted_msg_index = None
- L687:             st.session_state.pending_diffs    = {}
- L688:             st.session_state.pending_ai_label = ""
- L689:             st.session_state.pending_gsm_name = ""
- L690:             st.session_state.agent_running    = False
- L691:             st.session_state._import_key_done = ""
- L692:             st.session_state.preview_2d_data  = None
- L693:             st.session_state.preview_3d_data  = None
- L694:             st.session_state.preview_warnings = []
- L695:             st.session_state.preview_meta     = {"kind": "", "timestamp": ""}
- L698:             st.session_state.work_dir         = _keep_work_dir
- L699:             st.session_state.model_api_keys   = _keep_api_keys
- L700:             st.session_state.chat_history     = _keep_chat
- L711:         feedback_path = Path(st.session_state.work_dir) / "feedback.jsonl"
- L733:         st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
- L734:         return False, st.session_state.tapir_last_error
- L737:     st.session_state.tapir_selected_guids = guids
- L738:     st.session_state.tapir_last_sync_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
- L741:         st.session_state.tapir_selected_details = []
- L742:         st.session_state.tapir_selected_params = []
- L743:         st.session_state.tapir_param_edits = {}
- L744:         st.session_state.tapir_last_error = ""
- L748:     st.session_state.tapir_selected_details = details
- L749:     st.session_state.tapir_last_error = ""
- L760:         st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
- L761:         return False, st.session_state.tapir_last_error
- L763:     guids = st.session_state.get("tapir_selected_guids") or []
- L769:         st.session_state.tapir_last_error = "高亮失败"
- L770:         return False, st.session_state.tapir_last_error
- L772:     st.session_state.tapir_last_error = ""
- L783:         st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
- L784:         return False, st.session_state.tapir_last_error
- L786:     guids = st.session_state.get("tapir_selected_guids") or []
- L792:         st.session_state.tapir_selected_params = []
- L793:         st.session_state.tapir_param_edits = {}
- L794:         st.session_state.tapir_last_error = "未读取到可编辑参数（可能包含非 GDL 元素）"
- L795:         return False, st.session_state.tapir_last_error
- L844:     st.session_state.tapir_selected_params = selected_params
- L845:     st.session_state.tapir_param_edits = edit_map
- L848:         st.session_state.tapir_last_error = "未读取到可编辑参数（可能全为非 GDL 元素）"
- L849:         return False, st.session_state.tapir_last_error
- L852:         st.session_state.tapir_last_error = f"已跳过 {skipped} 个不可读取参数的元素"
- L855:     st.session_state.tapir_last_error = ""
- L866:         st.session_state.tapir_last_error = "Archicad 未运行或 Tapir 未安装"
- L867:         return False, st.session_state.tapir_last_error
- L869:     rows = st.session_state.get("tapir_selected_params") or []
- L873:     edits = st.session_state.get("tapir_param_edits") or {}
- L926:             st.session_state.tapir_last_error = f"参数转换失败：{', '.join(conversion_errors[:6])}"
- L927:             return False, st.session_state.tapir_last_error
- L938:         st.session_state.tapir_last_error = "Tapir 未返回执行结果"
- L939:         return False, st.session_state.tapir_last_error
- L948:         st.session_state.tapir_last_error = f"部分写回失败：{fail_text}"
- L950:         return False, st.session_state.tapir_last_error + suffix
- L952:     st.session_state.tapir_last_error = ""
- L959:     guids = st.session_state.get("tapir_selected_guids") or []
- L960:     details = st.session_state.get("tapir_selected_details") or []
- L961:     last_sync = st.session_state.get("tapir_last_sync_at", "")
- L962:     last_error = st.session_state.get("tapir_last_error", "")
- L985:     rows = st.session_state.get("tapir_selected_params") or []
- L990:     edits = st.session_state.get("tapir_param_edits") or {}
- L1013:     st.session_state.tapir_param_edits = edits
- L1023:         code = (st.session_state.project or HSFProject.create_new("untitled")).get_script(stype) or ""
- L1038:                 if st.session_state.project:
- L1039:                     st.session_state.project.set_script(stype, new_code)
- L1074:     user_kb_dir = Path(st.session_state.work_dir) / "knowledge"
- L1081:     if st.session_state.get("pro_unlocked", False):
- L1082:         pro_kb_dir = Path(st.session_state.work_dir) / "pro_knowledge"
- L1097:     user_sk_dir = Path(st.session_state.work_dir) / "skills"
- L1417:         st.session_state._ace_pending_main_editor_keys = set()
- L1419:     st.session_state._ace_pending_main_editor_keys = {
- L1426:     st.session_state.editor_version = int(st.session_state.get("editor_version", 0)) + 1
- L1427:     _mark_main_ace_editors_pending(st.session_state.editor_version)
- L1428:     return st.session_state.editor_version
- L1527:             m for m in st.session_state.chat_history[-8:]
- L1580:                     st.session_state.pending_gsm_name = gsm_name
- L1587:                 st.session_state.pending_diffs    = cleaned
- L1588:                 st.session_state.pending_ai_label = label_str
- L1590:                     st.session_state.pending_gsm_name = gsm_name
- L1693:         st.session_state.script_revision = int(st.session_state.get("script_revision", 0)) + 1
- L1694:     _rev = int(st.session_state.get("script_revision", 0))
- L1713:         st.session_state.preview_2d_data = None
- L1714:         st.session_state.preview_3d_data = None
- L1715:         st.session_state.preview_warnings = []
- L1716:         st.session_state.preview_meta = {"kind": "", "timestamp": ""}
- L1727:         _requested_rev = int(st.session_state.get("script_revision", 0)) or 1
- L1728:         _compile_rev = _safe_compile_revision(gsm_name or proj.name, st.session_state.work_dir, _requested_rev)
- L1730:             st.session_state.script_revision = _compile_rev
- L1731:         output_gsm = _versioned_gsm_path(gsm_name or proj.name, st.session_state.work_dir, revision=_compile_rev)
- L1737:             st.session_state.compile_log.append({
- L1746:             st.session_state.compile_log.append({
- L1841:         proj.work_dir = Path(st.session_state.work_dir)
- L1881:     proj.work_dir = Path(st.session_state.work_dir)
- L1883:     st.session_state.project = proj
- L1884:     st.session_state.pending_diffs = {}
- L1885:     st.session_state.preview_2d_data = None
- L1886:     st.session_state.preview_3d_data = None
- L1887:     st.session_state.preview_warnings = []
- L1888:     st.session_state.preview_meta = {"kind": "", "timestamp": ""}
- L1890:     st.session_state.pending_gsm_name = _import_gsm_name
- L1891:     st.session_state.script_revision = 0
- L1894:     st.session_state.chat_history.append({"role": "assistant", "content": msg})
- L1957:     for msg in st.session_state.get("chat_history", []):
- L2030:     if st.session_state.get("project"):
- L2129:                 st.session_state.pending_diffs    = extracted
- L2130:                 st.session_state.pending_ai_label = label_str
- L2322:     pending_keys = st.session_state.get("_ace_pending_main_editor_keys") or set()
- L2326:         if editor_key not in st.session_state:
- L2328:         raw_value = st.session_state.get(editor_key)
- L2340:     st.session_state._ace_pending_main_editor_keys = pending_keys
- L2343:         st.session_state.preview_2d_data = None
- L2344:         st.session_state.preview_3d_data = None
- L2345:         st.session_state.preview_warnings = []
- L2346:         st.session_state.preview_meta = {"kind": "", "timestamp": ""}
- L2490:     _sync_visible_editor_buffers(proj, int(st.session_state.get("editor_version", 0)))
- L2498:             st.session_state.preview_2d_data = res_2d
- L2499:             st.session_state.preview_warnings = _dedupe_keep_order([*pre_warns, *res_2d.warnings])
- L2500:             st.session_state.preview_meta = {"kind": "2D", "timestamp": ts}
- L2505:             st.session_state.preview_3d_data = res_3d
- L2506:             st.session_state.preview_warnings = _dedupe_keep_order([*pre_warns, *res_3d.warnings])
- L2507:             st.session_state.preview_meta = {"kind": "3D", "timestamp": ts}
- L2513:         st.session_state.preview_warnings = _dedupe_keep_order([
- L2517:         st.session_state.preview_meta = {"kind": target.upper(), "timestamp": ts}
- L2566: if not st.session_state.project:
- L2567:     st.session_state.project = HSFProject.create_new(
- L2568:         "untitled", work_dir=st.session_state.work_dir
- L2570:     st.session_state.script_revision = 0
- L2571: proj_now = st.session_state.project
- L2572: _ev      = st.session_state.editor_version
- L2583:                 disabled=st.session_state.agent_running,
- L2588:                 if st.session_state._import_key_done != _fkey:
- L2591:                         st.session_state._import_key_done = _fkey
- L2600:                 value=st.session_state.pending_gsm_name or proj_now.name,
- L2604:             st.session_state.pending_gsm_name = gsm_name_input
- L2607:                          disabled=st.session_state.agent_running):
- L2614:                 st.session_state.compile_result = (success, result_msg)
- L2619:         if st.session_state.compile_result is not None:
- L2620:             _c_ok, _c_msg = st.session_state.compile_result
- L2634:                         st.session_state.tapir_test_trigger = True
- L2642:                         st.session_state.tapir_selection_trigger = True
- L2646:                         st.session_state.tapir_highlight_trigger = True
- L2650:                         st.session_state.tapir_load_params_trigger = True
- L2653:                     _can_apply = bool(st.session_state.get("tapir_selected_params"))
- L2655:                         st.session_state.tapir_apply_params_trigger = True
- L2681:                 st.session_state.confirm_clear = True
- L2685:                 st.session_state["_show_log_dialog"] = True
- L2707:             if not st.session_state.compile_log:
- L2710:                 for _entry in reversed(st.session_state.compile_log):
- L2716:                 st.session_state.compile_log = []
- L2717:                 st.session_state.compile_result = None
- L2720:         if st.session_state.get("_show_log_dialog"):
- L2721:             st.session_state["_show_log_dialog"] = False
- L2724:         if st.session_state.get("confirm_clear"):
- L2729:                     _keep_work_dir = st.session_state.work_dir
- L2730:                     _keep_api_keys = st.session_state.model_api_keys
- L2731:                     _keep_chat     = st.session_state.chat_history   # preserve chat
- L2732:                     st.session_state.project          = None
- L2733:                     st.session_state.compile_log      = []
- L2734:                     st.session_state.compile_result   = None
- L2735:                     st.session_state.pending_diffs    = {}
- L2736:                     st.session_state.pending_ai_label = ""
- L2737:                     st.session_state.pending_gsm_name = ""
- L2738:                     st.session_state.script_revision  = 0
- L2739:                     st.session_state.agent_running    = False
- L2740:                     st.session_state._import_key_done = ""
- L2741:                     st.session_state.confirm_clear    = False
- L2742:                     st.session_state.preview_2d_data  = None
- L2743:                     st.session_state.preview_3d_data  = None
- L2744:                     st.session_state.preview_warnings = []
- L2745:                     st.session_state.preview_meta     = {"kind": "", "timestamp": ""}
- L2748:                     st.session_state.work_dir         = _keep_work_dir
- L2749:                     st.session_state.model_api_keys   = _keep_api_keys
- L2750:                     st.session_state.chat_history     = _keep_chat
- L2755:                     st.session_state.confirm_clear = False
- L2759:         _pm = st.session_state.get("preview_meta") or {}
- L2767:             _render_preview_2d(st.session_state.get("preview_2d_data"))
- L2769:             _render_preview_3d(st.session_state.get("preview_3d_data"))
- L2771:             _warns = st.session_state.get("preview_warnings") or []
- L2814:                     pending_keys = st.session_state.get("_ace_pending_main_editor_keys", set())
- L2820:                             st.session_state._ace_pending_main_editor_keys = pending_keys
- L2830:                     st.session_state.preview_2d_data = None
- L2831:                     st.session_state.preview_3d_data = None
- L2832:                     st.session_state.preview_warnings = []
- L2833:                     st.session_state.preview_meta = {"kind": "", "timestamp": ""}
- L2906:                 st.session_state.chat_history = []
- L2907:                 st.session_state.adopted_msg_index = None
- L2908:                 st.session_state.chat_anchor_focus = None
- L2911:         _anchors = _build_chat_script_anchors(st.session_state.chat_history)
- L2918:                 _focus = st.session_state.get("chat_anchor_focus")
- L2936:                     st.session_state.chat_anchor_pending = _picked["msg_idx"]
- L2939:         for _i, _msg in enumerate(st.session_state.chat_history):
- L2940:             _is_focus = st.session_state.get("chat_anchor_focus") == _i
- L2958:                             st.session_state[f"_show_dislike_{_i}"] = True
- L2960:                     if st.session_state.get(f"_show_dislike_{_i}"):
- L2973:                                     st.session_state[f"_show_dislike_{_i}"] = False
- L2978:                                     st.session_state[f"_show_dislike_{_i}"] = False
- L2983:                             st.session_state[_flag] = not st.session_state.get(_flag, False)
- L2986:                             (st.session_state.chat_history[j]["content"]
- L2988:                              if st.session_state.chat_history[j]["role"] == "user"),
- L2992:                             st.session_state.chat_history = st.session_state.chat_history[:_i]
- L2993:                             st.session_state["_redo_input"] = _prev_user
- L3004:                                 _is_adopted = st.session_state.adopted_msg_index == _i
- L3007:                                     st.session_state["_pending_adopt_idx"] = _i
- L3008:             if st.session_state.get(f"_showcopy_{_i}", False):
- L3017:                     _msg_content = st.session_state.chat_history[msg_idx]["content"]
- L3021:                         if st.session_state.project:
- L3022:                             _apply_scripts_to_project(st.session_state.project, extracted)
- L3024:                         st.session_state.adopted_msg_index = msg_idx
- L3025:                         st.session_state["_pending_adopt_idx"] = None
- L3032:                     st.session_state["_pending_adopt_idx"] = None
- L3035:         if st.session_state.get("_pending_adopt_idx") is not None:
- L3036:             _adopt_confirm_dialog(st.session_state["_pending_adopt_idx"])
- L3038:         if st.session_state.pending_diffs:
- L3039:             _pd = st.session_state.pending_diffs
- L3045:             _pd_label = "、".join(_pd_parts) or st.session_state.pending_ai_label or "新内容"
- L3061:                     _proj = st.session_state.project
- L3069:                     st.session_state.pending_diffs    = {}
- L3070:                     st.session_state.pending_ai_label = ""
- L3075:                     st.session_state.pending_diffs    = {}
- L3076:                     st.session_state.pending_ai_label = ""
- L3082:         _dbg_active = st.session_state.get("_debug_mode_active") == "editor"
- L3092:             st.session_state["_debug_mode_active"] = "editor" if _dbg_active else None
- L3109:         if st.session_state.agent_running:
- L3116:             disabled=st.session_state.agent_running,
- L3141:     _redo_input                = st.session_state.pop("_redo_input", None)
- L3142:     _active_dbg                = st.session_state.get("_debug_mode_active")
- L3143:     _tapir_trigger             = st.session_state.pop("tapir_test_trigger", False)
- L3144:     _tapir_selection_trigger   = st.session_state.pop("tapir_selection_trigger", False)
- L3145:     _tapir_highlight_trigger   = st.session_state.pop("tapir_highlight_trigger", False)
- L3146:     _tapir_load_params_trigger = st.session_state.pop("tapir_load_params_trigger", False)
- L3147:     _tapir_apply_params_trigger = st.session_state.pop("tapir_apply_params_trigger", False)
- L3151:     _anchor_pending = st.session_state.pop("chat_anchor_pending", None)
- L3157:         _proj_for_tapir = st.session_state.project
- L3165:             st.session_state.chat_history.append({
- L3172:                 st.session_state.chat_history.append({
- L3176:                 st.session_state["_auto_debug_input"] = _auto_debug
- L3184:             if st.session_state.get("tapir_selected_guids"):
- L3203:             if st.session_state.get("tapir_last_error"):
- L3204:                 st.warning(st.session_state.tapir_last_error)
- L3218:     _auto_debug_input = st.session_state.pop("_auto_debug_input", None)
- L3224:         st.session_state["_debug_mode_active"] = None
- L3232:     if user_input and not (st.session_state.pending_gsm_name or "").strip():
- L3235:             st.session_state.pending_gsm_name = _gsm_candidate
- L3244:         _route_pick = st.session_state.get("chat_image_route_mode", "自动")
- L3253:         st.session_state.chat_history.append({
- L3263:             st.session_state.chat_history.append({"role": "assistant", "content": err})
- L3267:                 st.session_state.agent_running = True
- L3269:                 if not st.session_state.project:
- L3271:                     _vproj = HSFProject.create_new(_vname, work_dir=st.session_state.work_dir)
- L3272:                     st.session_state.project = _vproj
- L3273:                     st.session_state.pending_gsm_name = _vname
- L3274:                     st.session_state.script_revision = 0
- L3276:                 _proj_v = st.session_state.project
- L3302:                                 gsm_name=(st.session_state.pending_gsm_name or _proj_v.name),
- L3309:                 st.session_state.chat_history.append({"role": "assistant", "content": msg})
- L3312:                 st.session_state.agent_running = False
- L3318:             st.session_state.chat_history.append({"role": "user", "content": effective_input})
- L3323:             st.session_state.chat_history.append({"role": "assistant", "content": err})
- L3327:                 st.session_state.agent_running = True
- L3331:                     project_loaded=bool(st.session_state.project),
- L3340:                                 st.session_state.chat_history[:-1],
- L3345:                             if not st.session_state.project:
- L3346:                                 new_proj = HSFProject.create_new(gdl_obj_name, work_dir=st.session_state.work_dir)
- L3347:                                 st.session_state.project = new_proj
- L3348:                                 st.session_state.pending_gsm_name = gdl_obj_name
- L3349:                                 st.session_state.script_revision = 0
- L3352:                             proj_current = st.session_state.project
- L3358:                             effective_gsm = st.session_state.pending_gsm_name or proj_current.name
- L3366:                 st.session_state.chat_history.append({"role": "assistant", "content": msg})
- L3369:                 st.session_state.agent_running = False
- L3374:         st.session_state.chat_anchor_focus = _anchor_pending

## 技术债清单
- openbrep/core.py:65         self.auto_rewrite = False  # validator规则不足时暂时关闭，成熟后改回True

## 建议优先处理
1. 缺少 tests/ 目录，建议优先补核心流程与边界条件测试。
2. ui/app.py 行数很大，建议拆分模块并减少单文件复杂度。
3. 存在 31 个超过 50 行的函数，建议拆分。
4. 存在 47 处深度嵌套（>4），建议扁平化控制流。
5. 核心文件存在 if/elif 长链，建议用表驱动或映射简化路由逻辑。
