"""Downstream TUI App class — extension seam for project-specific customizations.

This subclass overrides the project-specific methods identified in the
F-34 Phase 4 analysis, each calling super() to preserve exact upstream
behavior by default. Downstream customizers replace individual methods
to change behavior without touching unrelated code.

Override map (mirrors F-34 Phase 4 option A):
  _resolve_theme_name()       → swap theme config source
  _collect_mcp_servers()      → swap MCP config source
  _list_available_models()   → swap model discovery source
  _open_phase2_dialog()      → add/redirect dialog names
  _show_resume_browser()     → swap session browser screen
  _on_session_selected()     → rewire context after resume
  on_mount()                  → swap startup screen / add setup
  handle_local_slash_command() → add downstream slash commands
"""

from __future__ import annotations

from src.tui.app import ClawCodexTUI


class ClawCodexExtTUI(ClawCodexTUI):
    """Downstream-owned TUI App — override hooks for project-specific behavior."""

    # ---- theme ----

    def _resolve_theme_name(self) -> str:
        """Resolve the initial theme name.

        Default implementation reads from ``src.config``. Override to read
        from a downstream config namespace instead.
        """
        return super()._resolve_theme_name()

    # ---- MCP servers ----

    def _collect_mcp_servers(self):
        """Collect MCP server definitions from config.

        Default implementation reads from ``src.config.load_config()``.
        Override to read from a downstream config source.
        """
        return super()._collect_mcp_servers()

    # ---- model discovery ----

    def _list_available_models(self) -> list[str]:
        """Return available models for the active provider.

        Default implementation uses ``src.config.get_provider_config``.
        Override to restrict or augment the model list.
        """
        return super()._list_available_models()

    # ---- Phase 2 dialog dispatcher ----

    def _open_phase2_dialog(self, name: str, transcript) -> None:
        """Push the modal screen for dialog ``name``.

        Default implementation handles the standard set of slash-command
        dialogs. Override to add new dialog names or redirect existing
        ones to custom screen classes.
        """
        return super()._open_phase2_dialog(name, transcript)

    # ---- session / resume ----

    def _show_resume_browser(self) -> None:
        """Push the session-browser screen for --resume without SESSION_ID.

        Default implementation pushes ``ResumeConversation``.
        Override to substitute a downstream session browser.
        """
        return super()._show_resume_browser()

    def _on_session_selected(self, session_id: str | None) -> None:
        """Callback after the user picks a session from the resume browser.

        Default implementation swaps the session and replays history.
        Override to add downstream-specific post-resume wiring.
        """
        return super()._on_session_selected(session_id)

    # ---- startup ----

    def on_mount(self) -> None:
        """Called when the app is first mounted.

        Default implementation sets up stylesheet, pushes REPLScreen, and
        restores focus. Override to add downstream-only initialization;
        call super() to preserve the standard bootstrap sequence.
        """
        # Install F-43 runtime commands + observer before the user can
        # issue a /provider or /model slash command. The TUI dispatches
        # through the global command registry, so a single registration
        # here is enough for the lifetime of the app.
        try:
            from clawcodex_ext.frontend.tui_extensions import install_tui_extensions

            install_tui_extensions(self, self.runtime_context)
        except Exception:
            pass
        return super().on_mount()

    # ---- slash commands ----

    def handle_local_slash_command(self, text: str, transcript) -> bool:
        """Handle a slash-command entered at the prompt.

        Default implementation dispatches to built-in commands.
        Override to inject downstream-only commands before or after
        the upstream dispatcher.
        """
        return super().handle_local_slash_command(text, transcript)