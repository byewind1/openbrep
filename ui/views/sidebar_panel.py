from __future__ import annotations

from typing import Callable

from openbrep.config import GDLAgentConfig, model_to_provider


def render_sidebar(
    st,
    *,
    openbrep_version: str,
    tapir_import_ok: bool,
    all_models: list[str],
    config,
    config_defaults: dict,
    custom_providers: list[dict],
    iter_custom_provider_model_entries_fn: Callable[[dict], object],
    is_generation_locked_fn: Callable[[object], bool],
    is_archicad_running_fn: Callable[[], bool],
    render_generation_controls_fn: Callable[[], None],
    has_streamlit_runtime_context_fn: Callable[[], bool],
    load_license_fn: Callable[[str], dict],
    license_record_is_active_fn: Callable[[dict], tuple[bool, str, dict | None]],
    save_license_fn: Callable[[str, dict], None],
    empty_license_record_fn: Callable[[], dict],
    verify_pro_code_fn: Callable[[str], tuple[bool, str, dict | None]],
    import_pro_knowledge_zip_fn: Callable[[bytes, str, str], tuple[bool, str]],
    normalize_converter_path_fn: Callable[[str], str],
    reload_config_globals_fn: Callable[..., None],
    build_model_source_state_fn: Callable[..., dict],
    resolve_selected_model_fn: Callable[[str, list[dict]], str],
    sync_llm_top_level_fields_for_model_fn: Callable[[GDLAgentConfig, str], bool],
    key_for_model_fn: Callable[[str], str],
    collect_custom_model_aliases_fn: Callable[[list[dict]], list[str]],
    should_persist_assistant_settings_fn: Callable[[str, str], bool],
    reset_tapir_p0_state_fn: Callable[[], None],
    bump_main_editor_version_fn: Callable[[], int],
) -> dict:
    if not st.session_state.assistant_settings:
        st.session_state.assistant_settings = config_defaults.get("assistant_settings", "")
    if tapir_import_ok and not is_archicad_running_fn():
        st.sidebar.warning("⚠️ Archicad 未运行，编译和实时预览不可用")

    st.markdown('<p class="main-header">OpenBrep</p>', unsafe_allow_html=True)
    st.markdown('<p class="intro-header">用自然语言驱动 ArchiCAD GDL 库对象的创建、修改与编译。</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="sub-header">OpenBrep: Code Your Boundaries · v{openbrep_version} · HSF-native</p>',
        unsafe_allow_html=True,
    )
    render_generation_controls_fn()
    st.divider()

    st.divider()
    _render_assistant_settings(
        st,
        config_defaults=config_defaults,
        should_persist_assistant_settings_fn=should_persist_assistant_settings_fn,
    )

    work_dir = _render_work_dir(st, is_generation_locked_fn=is_generation_locked_fn)
    _render_pro_section(
        st,
        work_dir=work_dir,
        has_streamlit_runtime_context_fn=has_streamlit_runtime_context_fn,
        load_license_fn=load_license_fn,
        license_record_is_active_fn=license_record_is_active_fn,
        save_license_fn=save_license_fn,
        empty_license_record_fn=empty_license_record_fn,
        verify_pro_code_fn=verify_pro_code_fn,
        import_pro_knowledge_zip_fn=import_pro_knowledge_zip_fn,
    )

    st.divider()
    compiler_mode, converter_path = _render_compiler_section(
        st,
        config_defaults=config_defaults,
        normalize_converter_path_fn=normalize_converter_path_fn,
    )

    st.divider()
    model_payload = _render_llm_section(
        st,
        all_models=all_models,
        config=config,
        config_defaults=config_defaults,
        custom_providers=custom_providers,
        iter_custom_provider_model_entries_fn=iter_custom_provider_model_entries_fn,
        is_generation_locked_fn=is_generation_locked_fn,
        reload_config_globals_fn=reload_config_globals_fn,
        build_model_source_state_fn=build_model_source_state_fn,
        resolve_selected_model_fn=resolve_selected_model_fn,
        sync_llm_top_level_fields_for_model_fn=sync_llm_top_level_fields_for_model_fn,
        key_for_model_fn=key_for_model_fn,
        collect_custom_model_aliases_fn=collect_custom_model_aliases_fn,
    )

    _persist_converter_path(st, converter_path=converter_path, config_defaults=config_defaults)

    st.divider()
    _render_project_reset(
        st,
        is_generation_locked_fn=is_generation_locked_fn,
        reset_tapir_p0_state_fn=reset_tapir_p0_state_fn,
        bump_main_editor_version_fn=bump_main_editor_version_fn,
    )

    return {
        "work_dir": work_dir,
        "compiler_mode": compiler_mode,
        "converter_path": converter_path,
        **model_payload,
    }


def _render_work_dir(st, *, is_generation_locked_fn: Callable[[object], bool]) -> str:
    st.subheader("📁 工作目录")
    work_dir = st.text_input(
        "Work Directory",
        value=st.session_state.work_dir,
        label_visibility="collapsed",
        disabled=is_generation_locked_fn(st.session_state),
    )
    st.session_state.work_dir = work_dir
    return work_dir


def _render_pro_section(
    st,
    *,
    work_dir: str,
    has_streamlit_runtime_context_fn: Callable[[], bool],
    load_license_fn: Callable[[str], dict],
    license_record_is_active_fn: Callable[[dict], tuple[bool, str, dict | None]],
    save_license_fn: Callable[[str, dict], None],
    empty_license_record_fn: Callable[[], dict],
    verify_pro_code_fn: Callable[[str], tuple[bool, str, dict | None]],
    import_pro_knowledge_zip_fn: Callable[[bytes, str, str], tuple[bool, str]],
) -> None:
    if not st.session_state.pro_license_loaded and has_streamlit_runtime_context_fn():
        lic = load_license_fn(work_dir)
        if bool(lic.get("pro_unlocked", False)):
            ok, _msg, normalized = license_record_is_active_fn(lic)
            if ok and normalized is not None:
                st.session_state.pro_unlocked = True
                save_license_fn(work_dir, normalized)
            else:
                st.session_state.pro_unlocked = False
                save_license_fn(work_dir, empty_license_record_fn())
        else:
            st.session_state.pro_unlocked = False
        st.session_state.pro_license_loaded = True

    st.subheader("🔐 Pro 授权（V1）")
    if st.session_state.pro_unlocked:
        st.success("Pro 已解锁")
    else:
        st.caption("当前：Free 模式（仅基础知识库）")

    pro_code_input = st.text_input("授权码", type="password", key="pro_code_input")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("解锁 Pro", width="stretch"):
            ok, msg, record = verify_pro_code_fn(pro_code_input)
            if ok and record is not None:
                st.session_state.pro_unlocked = True
                save_license_fn(work_dir, record)
                st.success("✅ Pro 解锁成功")
                st.rerun()
            else:
                st.error(msg)
    with c2:
        if st.button("锁回 Free", width="stretch"):
            st.session_state.pro_unlocked = False
            save_license_fn(work_dir, empty_license_record_fn())
            st.info("已切回 Free 模式")
            st.rerun()

    if st.session_state.pro_unlocked:
        pro_pkg = st.file_uploader("导入 Pro 知识包（.zip/.obrk）", type=["zip", "obrk"], key="pro_pkg_uploader")
        if pro_pkg is not None:
            ok, msg = import_pro_knowledge_zip_fn(pro_pkg.read(), pro_pkg.name, work_dir)
            if ok:
                st.success(msg)
            else:
                st.error(msg)
    else:
        st.caption("请先输入有效授权码并解锁后再导入知识包。")


def _render_compiler_section(st, *, config_defaults: dict, normalize_converter_path_fn: Callable[[str], str]) -> tuple[str, str]:
    st.subheader("🔧 编译器 / Compiler")
    compiler_mode = st.radio(
        "编译模式",
        ["Mock (无需 ArchiCAD)", "LP_XMLConverter (真实编译)"],
        index=1 if config_defaults.get("compiler_path") else 0,
    )

    converter_path = ""
    if compiler_mode.startswith("LP"):
        raw_path = st.text_input(
            "LP_XMLConverter 路径",
            value=config_defaults.get("compiler_path", ""),
            placeholder="/Applications/GRAPHISOFT/ArchiCAD 28/LP_XMLConverter.app/Contents/MacOS/LP_XMLConverter",
            help="macOS/Linux 用正斜杠 /，Windows 用反斜杠 粘贴后自动转换",
        )
        converter_path = normalize_converter_path_fn(raw_path)
    return compiler_mode, converter_path


def _render_llm_section(
    st,
    *,
    all_models: list[str],
    config,
    config_defaults: dict,
    custom_providers: list[dict],
    iter_custom_provider_model_entries_fn: Callable[[dict], object],
    is_generation_locked_fn: Callable[[object], bool],
    reload_config_globals_fn: Callable[..., None],
    build_model_source_state_fn: Callable[..., dict],
    resolve_selected_model_fn: Callable[[str, list[dict]], str],
    sync_llm_top_level_fields_for_model_fn: Callable[[GDLAgentConfig, str], bool],
    key_for_model_fn: Callable[[str], str],
    collect_custom_model_aliases_fn: Callable[[list[dict]], list[str]],
) -> dict:
    st.subheader("🧠 AI 模型 / LLM")
    reload_col, status_col = st.columns([1, 2])
    with reload_col:
        if st.button("重新加载配置", width="stretch", disabled=is_generation_locked_fn(st.session_state)):
            try:
                reload_config_globals_fn(update_session_state=True)
                st.session_state["current_model"] = config_defaults.get("llm_model", "")
                st.session_state["reload_config_notice"] = "✅ 已从磁盘重新加载 config.toml"
                st.rerun()
            except Exception as exc:
                st.warning(f"配置重载失败：{exc}")
    with status_col:
        reload_notice = st.session_state.pop("reload_config_notice", "")
        if reload_notice:
            st.caption(reload_notice)

    custom_list = config.llm.custom_providers if config else custom_providers
    model_state = build_model_source_state_fn(
        builtin_models=all_models,
        custom_providers=custom_list,
        saved_model=config_defaults.get("llm_model", "glm-4-flash"),
    )

    source_options = model_state["source_options"]
    default_source = model_state["default_source"]
    default_source_index = source_options.index(default_source) if default_source in source_options else 0
    selected_source = st.selectbox(
        "来源 / Source",
        source_options,
        index=default_source_index,
        disabled=is_generation_locked_fn(st.session_state),
    )

    active_options = model_state["custom_options"] if selected_source == "自定义" else model_state["builtin_options"]
    model_labels = [opt["label"] for opt in active_options]
    default_label = model_state["default_model_label"] if selected_source == default_source else ""
    default_model_index = model_labels.index(default_label) if default_label in model_labels else 0
    selected_label = st.selectbox(
        "模型 / Model",
        model_labels,
        index=default_model_index,
        disabled=is_generation_locked_fn(st.session_state),
    )
    model_name = resolve_selected_model_fn(selected_label, active_options)
    st.session_state["current_model"] = model_name

    _persist_model_choice(
        st,
        model_name=model_name,
        config_defaults=config_defaults,
        sync_llm_top_level_fields_for_model_fn=sync_llm_top_level_fields_for_model_fn,
    )

    if model_name not in st.session_state.model_api_keys:
        st.session_state.model_api_keys[model_name] = key_for_model_fn(model_name)

    is_custom = model_name in collect_custom_model_aliases_fn(custom_list)
    if is_custom:
        st.info("此模型使用自定义代理，请在 config.toml 的 [[llm.custom_providers]] 中配置 api_key 和 base_url")
        api_key = st.session_state.model_api_keys.get(model_name, "")
    else:
        api_key = st.text_input(
            "API Key",
            value=st.session_state.model_api_keys.get(model_name, ""),
            type="password",
            help="Ollama 本地模式不需要 Key",
            disabled=is_generation_locked_fn(st.session_state),
        )
    _persist_api_key(st, model_name=model_name, api_key=api_key)
    _render_provider_key_hint(st, model_name)

    default_api_base = _get_default_api_base(
        model_name,
        custom_providers=custom_providers,
        iter_custom_provider_model_entries_fn=iter_custom_provider_model_entries_fn,
    )
    api_base = st.text_input("API Base URL", value=default_api_base) if default_api_base else ""
    max_retries = st.slider("最大重试次数", 1, 10, 5)
    return {
        "model_name": model_name,
        "api_key": api_key,
        "api_base": api_base,
        "max_retries": max_retries,
    }


def _persist_model_choice(
    st,
    *,
    model_name: str,
    config_defaults: dict,
    sync_llm_top_level_fields_for_model_fn: Callable[[GDLAgentConfig, str], bool],
) -> None:
    if not (model_name and model_name != config_defaults.get("llm_model", "")):
        return
    try:
        save_cfg_model = GDLAgentConfig.load()
        if sync_llm_top_level_fields_for_model_fn(save_cfg_model, model_name):
            save_cfg_model.save()
        config_defaults["llm_model"] = model_name
    except Exception as exc:
        st.sidebar.warning(f"配置保存失败：{exc}")


def _persist_api_key(st, *, model_name: str, api_key: str) -> None:
    if api_key == st.session_state.model_api_keys.get(model_name, ""):
        return
    st.session_state.model_api_keys[model_name] = api_key
    try:
        save_cfg = GDLAgentConfig.load()
        provider = model_to_provider(model_name)
        if provider and api_key:
            save_cfg.llm.provider_keys[provider] = api_key
        save_cfg.save()
    except Exception as exc:
        st.sidebar.warning(f"配置保存失败：{exc}")


def _persist_converter_path(st, *, converter_path: str, config_defaults: dict) -> None:
    if not (converter_path and converter_path != config_defaults.get("compiler_path", "")):
        return
    try:
        save_cfg = GDLAgentConfig.load()
        save_cfg.compiler.path = converter_path
        save_cfg.save()
        config_defaults["compiler_path"] = converter_path
    except Exception as exc:
        st.sidebar.warning(f"配置保存失败：{exc}")


def _render_provider_key_hint(st, model_name: str) -> None:
    if "claude" in model_name:
        st.caption("🔑 [获取 Claude API Key →](https://console.anthropic.com/settings/keys)")
        st.caption("⚠️ API Key 需单独充值，与 Claude Pro 订阅额度无关")
    elif "glm" in model_name:
        st.caption("🔑 [获取智谱 API Key →](https://bigmodel.cn/usercenter/apikeys)")
    elif "gpt" in model_name or "o3" in model_name:
        st.caption("🔑 [获取 OpenAI API Key →](https://platform.openai.com/api-keys)")
    elif "deepseek" in model_name and "ollama" not in model_name:
        st.caption("🔑 [获取 DeepSeek API Key →](https://platform.deepseek.com/api_keys)")
    elif "gemini" in model_name:
        st.caption("🔑 [获取 Gemini API Key →](https://aistudio.google.com/apikey)")
    elif "ollama" in model_name:
        st.caption("🖥️ 本地运行，无需 Key。确保 Ollama 已启动。")


def _get_default_api_base(
    model_name: str,
    *,
    custom_providers: list[dict],
    iter_custom_provider_model_entries_fn: Callable[[dict], object],
) -> str:
    model_lower = model_name.lower()
    for provider_config in custom_providers:
        aliases = {
            str(entry.get("alias", "") or "").lower()
            for entry in iter_custom_provider_model_entries_fn(provider_config)
        }
        if model_lower in aliases:
            return str(provider_config.get("base_url", "") or "")
    if "ollama" in model_lower:
        return "http://localhost:11434"
    return ""


def _render_assistant_settings(
    st,
    *,
    config_defaults: dict,
    should_persist_assistant_settings_fn: Callable[[str, str], bool],
) -> None:
    st.subheader("AI助手设置")
    assistant_settings = st.text_area(
        "长期协作偏好",
        value=st.session_state.assistant_settings,
        height=140,
        placeholder="例如：我是 GDL 初学者，请先解释再给最小修改；我主要改已有对象；赶项目时优先给可运行结果。",
        help="长期保存。可填写你的 GDL 经验、当前使用场景、沟通方式偏好与修改边界。修改后立即影响后续聊天与生成。",
        disabled=st.session_state.get("generation_status") in {"running", "cancelling"},
    )
    if should_persist_assistant_settings_fn(config_defaults.get("assistant_settings", ""), assistant_settings):
        st.session_state.assistant_settings = assistant_settings
        try:
            save_cfg = GDLAgentConfig.load()
            save_cfg.llm.assistant_settings = assistant_settings
            save_cfg.save()
            config_defaults["assistant_settings"] = assistant_settings
        except Exception as exc:
            st.sidebar.warning(f"配置保存失败：{exc}")
    else:
        st.session_state.assistant_settings = assistant_settings


def _render_project_reset(
    st,
    *,
    is_generation_locked_fn: Callable[[object], bool],
    reset_tapir_p0_state_fn: Callable[[], None],
    bump_main_editor_version_fn: Callable[[], int],
) -> None:
    if not st.session_state.project:
        return
    if not st.button("🗑️ 清除项目", width="stretch", disabled=is_generation_locked_fn(st.session_state)):
        return

    keep_work_dir = st.session_state.work_dir
    keep_api_keys = st.session_state.model_api_keys
    keep_chat = st.session_state.chat_history
    keep_assistant_settings = st.session_state.assistant_settings
    st.session_state.project = None
    st.session_state.compile_log = []
    st.session_state.compile_result = None
    st.session_state.adopted_msg_index = None
    st.session_state.pending_diffs = {}
    st.session_state.pending_ai_label = ""
    st.session_state.pending_gsm_name = ""
    st.session_state.agent_running = False
    st.session_state._import_key_done = ""
    st.session_state.preview_2d_data = None
    st.session_state.preview_3d_data = None
    st.session_state.preview_warnings = []
    st.session_state.preview_meta = {"kind": "", "timestamp": ""}
    reset_tapir_p0_state_fn()
    bump_main_editor_version_fn()
    st.session_state.work_dir = keep_work_dir
    st.session_state.model_api_keys = keep_api_keys
    st.session_state.chat_history = keep_chat
    st.session_state.assistant_settings = keep_assistant_settings
    st.rerun()
