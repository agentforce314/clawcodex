"""TUI frontend plugin for the downstream registry."""

from __future__ import annotations

from clawcodex_ext.frontend.protocol import FrontendPlugin
from clawcodex_ext.frontend.registry import register_frontend


@register_frontend
class TUIFrontend(FrontendPlugin):
    name = "tui"
    display_name = "Textual TUI"

    def run(self, ctx, argv: list[str]) -> int:
        from src.entrypoints.tui import TUIOptions

        from clawcodex_ext.tui.entrypoint import run_tui

        options = TUIOptions(
            provider_name=ctx.provider_name,
            model=ctx.options.model,
            max_turns=ctx.options.max_turns,
            allowed_tools=ctx.options.allowed_tools,
            disallowed_tools=ctx.options.disallowed_tools,
            stream=ctx.options.stream,
            permission_mode=ctx.options.permission_mode,
            is_bypass_permissions_mode_available=ctx.options.is_bypass_permissions_mode_available,
            workspace_root=ctx.workspace_root,
        )
        return run_tui(
            options,
            provider=ctx.provider,
            session=ctx.session,
            tool_registry=ctx.tool_registry,
            tool_context=ctx.tool_context,
            runtime_context=ctx,
            resume_session_id=ctx.options.resume_session_id,
            resume_browse=ctx.options.resume_browse,
        )