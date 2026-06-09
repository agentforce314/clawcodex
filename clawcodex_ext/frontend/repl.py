"""REPL frontend plugin for the downstream registry."""

from __future__ import annotations

from clawcodex_ext.frontend.protocol import FrontendPlugin
from clawcodex_ext.frontend.registry import register_frontend


@register_frontend
class REPLFrontend(FrontendPlugin):
    name = "repl"
    display_name = "Interactive REPL"

    def run(self, ctx, argv: list[str]) -> int:
        from clawcodex_ext.repl.app import ClawCodexExtREPL

        from clawcodex_ext.frontend.repl_extensions import install_repl_extensions

        # Resolve session ID: --resume <id> or --resume (browse) via REPL browser
        session_id = ctx.options.resume_session_id
        if ctx.options.resume_browse and not session_id:
            from clawcodex_ext.repl.session_browser import browse_sessions_interactive
            from rich.console import Console
            console = Console()
            console.print("[dim]No session ID specified. Opening session browser...[/dim]")
            picked = browse_sessions_interactive(console=console)
            if picked:
                session_id = picked
            else:
                console.print("[yellow]No session selected. Starting a new session.[/yellow]")

        repl = ClawCodexExtREPL(
            provider_name=ctx.provider_name,
            stream=ctx.options.stream,
            permission_mode=ctx.options.permission_mode,
            is_bypass_permissions_mode_available=ctx.options.is_bypass_permissions_mode_available,
            resume_session_id=session_id,
            provider=ctx.provider,
            session=ctx.session,
            tool_registry=ctx.tool_registry,
            tool_context=ctx.tool_context,
            workspace_root=ctx.workspace_root,
            runtime_context=ctx,
            append_system_prompt=ctx.options.append_system_prompt,
        )
        install_repl_extensions(repl, ctx)
        repl.run()
        return 0