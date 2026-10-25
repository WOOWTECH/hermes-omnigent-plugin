"""Exact source transformations of Omnigent0.16 in the plugin-owned venv."""
from pathlib import Path

MARKER = '# hermes-omnigent-plugin compatibility v14'


def make_edits(sdk, source_overrides=None):
    from .runtime import Failure
    edits = []
    def change(relative, transform):
        file = Path(sdk) / relative
        original = (source_overrides or {}).get(file, file.read_text())
        if MARKER in original:
            edits.append((file, original, original))
            return
        patched = transform(original)
        if patched == original:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        compile(patched, str(file), 'exec')
        edits.append((file, original, MARKER + '\n' + patched))

    def host(source):
        anchor = '        with_source = functools.partial(_with_model_configuration_source, harness=harness)\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        return source.replace(anchor, anchor + '''        if harness == "hermes-native":
            from hermes_omnigent.compat import hermes_models
            try:
                rows = await asyncio.to_thread(hermes_models)
                return HostModelOptionsResultFrame(request_id=frame.request_id, status="ok", models=rows)
            except Exception:
                return HostModelOptionsResultFrame(request_id=frame.request_id, status="failed", error="Hermes model inventory unavailable")
''')

    def bridge(source):
        anchor = '_BRIDGE_ROOT = Path(tempfile.gettempdir()) / f"omnigent-{stable_user_id()}" / "hermes-native"\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, 'from hermes_omnigent.session_store import bridge_root as _profile_bridge_root\n'
            '_BRIDGE_ROOT = _profile_bridge_root(Path(tempfile.gettempdir()) / f"omnigent-{stable_user_id()}" / "hermes-native")\n')
        anchor = '    return _BRIDGE_ROOT / digest\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, '''    from hermes_omnigent.session_store import migrate_legacy
    target = _BRIDGE_ROOT / digest
    migrate_legacy(target)
    return target
''')
        expected = ['Path.home() / ".hermes" / "config.yaml"', 'Path.home() / ".hermes" / ".env"',
                    'Path.home() / ".hermes" / "auth.json"']
        for anchor in expected:
            if source.count(anchor) != 1:
                raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
            source = source.replace(anchor, anchor.replace('Path.home() / ".hermes"',
                'Path(os.environ.get("HERMES_OMNIGENT_PROFILE") or Path.home() / ".hermes")'))
        anchor = '        "model",\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, '        "_config_version",\n' + anchor)
        anchor = '            "command": sys.executable,\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        # Hermes 0.22 gives child tools a per-profile TMPDIR. The existing
        # bridge belongs to the runner's temp root; keep secure-dir validation
        # pointed at that same root, without weakening its ownership checks.
        source = source.replace(anchor, anchor + '            "env": {"TMPDIR": tempfile.gettempdir(), "HERMES_OMNIGENT_PROFILE": os.environ.get("HERMES_OMNIGENT_PROFILE", "")},\n')
        anchor = '    if _await_new_message(db_path, baseline_id or 0, _DELIVERY_CONFIRM_TIMEOUT_S):\n'
        if source.count(anchor) != 2:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, '    from hermes_omnigent.compat import await_hermes_message\n'
                                '    if await_hermes_message(db_path, baseline_id or 0, content, _DELIVERY_CONFIRM_TIMEOUT_S):\n')
        anchor = '    info = _wait_for_tmux_info(bridge_dir, timeout_s=timeout_s)\n    _run_tmux(info["socket_path"], "kill-session", "-t", info["tmux_target"])\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        return source.replace(anchor, '''    from hermes_omnigent.compat import native_gateway, stop_hermes_terminal
    try:
        stop_hermes_terminal(read_tmux_info(bridge_dir))
    finally:
        native_gateway("stop", read_hermes_home(bridge_dir))
''')

    def forwarder(source):
        anchor = '            return sqlite3.connect(uri, timeout=5.0)\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        # A read observer must not create state.db before Hermes initializes it.
        return source.replace(anchor, '            return sqlite3.connect(f"file:{db_path}?mode=rw", timeout=5.0, uri=True)\n')

    def orchestration(source):
        anchor = '    hermes_args = [*(launch_config.terminal_launch_args or [])]\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, anchor + '''    if launch_config.model_override and not any(arg == "--model" or arg.startswith("--model=") for arg in hermes_args):
        hermes_args.extend(["--model", launch_config.model_override])
''')
        start = source.index('async def _auto_create_hermes_terminal(')
        end = source.index('\nasync def ', start + 1)
        section = source[start:end]
        anchor = '    clear_hermes_bridge_state(bridge_dir)\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        section = section.replace(anchor, '''    from hermes_omnigent.compat import retained_hermes_state, resume_hermes_args
    _retained_hermes_state = retained_hermes_state(bridge_dir)
    if _retained_hermes_state is None:
        clear_hermes_bridge_state(bridge_dir)
''')
        section = section.replace('    clear_hermes_status_state(bridge_dir)\n',
                                  '    if _retained_hermes_state is None:\n        clear_hermes_status_state(bridge_dir)\n')
        anchor = '    if launch_config.fork_carry_history and launch_config.fork_source_external_id:\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        section = section.replace(anchor, '''    if _retained_hermes_state is not None:
        hermes_args = resume_hermes_args(hermes_args, _retained_hermes_state.hermes_session_id)
    if _retained_hermes_state is None and launch_config.fork_carry_history and launch_config.fork_source_external_id:
''')
        anchor = '                    hermes_args.extend(["--resume", _target_session_id])\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        section = section.replace(anchor, '                    hermes_args = resume_hermes_args(hermes_args, _target_session_id)\n')
        anchor = '    _hermes_terminal_env: dict[str, str] = {}\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        # Web terminals may attach after the OSC11 background probe's deadline;
        # its late response then becomes typed input. Use the supported explicit
        # dark palette for this terminal, so Hermes never sends that probe.
        section = section.replace(anchor, '    _hermes_terminal_env: dict[str, str] = {"HERMES_LIGHT": "0"}\n')
        anchor = '    terminal_view = await resource_registry.launch_required_terminal(\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        section = section.replace(anchor, '''    if _hermes_home_path is not None and os.environ.get("HERMES_OMNIGENT_DIRECT_HERMES") == "1":
        from hermes_omnigent.compat import native_gateway
        await asyncio.to_thread(native_gateway, "prepare", _hermes_home_path)
''' + anchor)
        anchor = '            os_env=OSEnvSpec(type="caller_process", cwd=workspace),\n'
        if section.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        section = section.replace('from omnigent.inner.datamodel import OSEnvSpec, TerminalEnvSpec',
            'from omnigent.inner.datamodel import OSEnvSpec, TerminalEnvSpec, OSEnvSandboxSpec')
        section = section.replace(anchor, '''            os_env=OSEnvSpec(type="caller_process", cwd=workspace,
                sandbox=OSEnvSandboxSpec(type="none") if os.environ.get("HERMES_OMNIGENT_DIRECT_HERMES") == "1" else None),
''')
        return source[:start] + section + source[end:]

    def runner(source):
        anchor = '        if body_type == "model_change":\n            harness = _session_harness_name(conversation_id)\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        source = source.replace(anchor, anchor + '''            if harness == "hermes-native":
                from hermes_omnigent.compat import change_model
                model = body.get("model") if isinstance(body, dict) else None
                if model is not None and not isinstance(model, str):
                    return JSONResponse({"error": "invalid_model"}, status_code=400)
                return await change_model(conversation_id, model)
''')
        anchor = '        harness = _session_harness_name(session_id)\n        if harness == "claude-native":\n            return await get_session_claude_model_options(session_id)\n'
        if source.count(anchor) != 1:
            raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
        return source.replace(anchor, '        harness = _session_harness_name(session_id)\n' + '''        if harness == "hermes-native":
            from hermes_omnigent.compat import hermes_models
            return JSONResponse({"models": await asyncio.to_thread(hermes_models)})
''' + '        if harness == "claude-native":\n            return await get_session_claude_model_options(session_id)\n')

    change('host/connect.py', host)
    change('harnesses/hermes_native/bridge.py', bridge)
    change('harnesses/hermes_native/forwarder.py', forwarder)
    change('runner/native/orchestration.py', orchestration)
    change('runner/app.py', runner)
    return edits
