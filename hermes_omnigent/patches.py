"""Exact source transformations of Omnigent0.16 in the plugin-owned venv."""
from pathlib import Path

MARKER = '# hermes-omnigent-plugin compatibility v2'


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
        expected = ['Path.home() / ".hermes" / "config.yaml"', 'Path.home() / ".hermes" / ".env"',
                    'Path.home() / ".hermes" / "auth.json"']
        for anchor in expected:
            if source.count(anchor) != 1:
                raise Failure('OMNIGENT_COMPAT_ANCHOR_MISSING')
            source = source.replace(anchor, anchor.replace('Path.home() / ".hermes"',
                'Path(os.environ.get("HERMES_OMNIGENT_PROFILE") or Path.home() / ".hermes")'))
        return source

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
    change('runner/native/orchestration.py', orchestration)
    change('runner/app.py', runner)
    return edits
