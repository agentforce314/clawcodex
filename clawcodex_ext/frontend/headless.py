"""Headless (print-mode) frontend plugin for the downstream registry."""

from __future__ import annotations

from clawcodex_ext.frontend.protocol import FrontendPlugin
from clawcodex_ext.frontend.registry import register_frontend


@register_frontend
class HeadlessFrontend(FrontendPlugin):
    name = "headless"
    display_name = "Headless / Print Mode"

    def run(self, ctx, argv: list[str]) -> int:
        from src.entrypoints.headless import HeadlessOptions, run_headless

        options = HeadlessOptions(
            prompt=getattr(ctx.options, 'prompt', None),
            output_format=getattr(ctx.options, 'output_format', 'text'),
            input_format=getattr(ctx.options, 'input_format', 'text'),
            provider_name=ctx.provider_name,
            model=ctx.options.model,
            max_turns=ctx.options.max_turns,
            permission_mode=ctx.options.permission_mode,
            is_bypass_permissions_mode_available=ctx.options.is_bypass_permissions_mode_available,
            skip_permissions=ctx.options.skip_permissions,
            allowed_tools=ctx.options.allowed_tools,
            disallowed_tools=ctx.options.disallowed_tools,
            include_partial_messages=getattr(ctx.options, 'include_partial_messages', False),
            verbose=ctx.options.verbose,
            workspace_root=ctx.workspace_root,
            provider=ctx.provider,
            session=ctx.session,
            tool_registry=ctx.tool_registry,
            tool_context=ctx.tool_context,
        )
        return run_headless(options)