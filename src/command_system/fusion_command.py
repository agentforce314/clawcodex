"""/fusion — create and manage fusion models (base reasoning + borrowed vision).

The management surface for :mod:`src.providers.fusion_models`; the CCR web
UI's Fusion Models pane (``packages/ui/src/pages/home/components/virtual-models.tsx``)
rendered as a slash command. That pane is a list with an ``Add`` button and
per-row enable/edit/remove; the same operations here:

    /fusion                                  list saved fusion models
    /fusion create <name> <base> <vision>    save a new one
    /fusion delete <name>                    remove one
    /fusion enable|disable <name>            toggle without losing config
    /fusion help                             usage

``<base>`` and ``<vision>`` are ``provider:model`` selectors, matching
``/advisor``'s grammar. CCR's dialog reads

    New model  =  Base model  +  Tools(vision → Vision model)

which is exactly ``create <name> <base> <vision>``.

Every path is headless-safe (no UI surface needed), so the command works
identically in the TUI, ``-p`` headless, and the SDK.
"""

from __future__ import annotations

from .types import CommandContext, LocalCommand, LocalCommandResult

_USAGE = (
    "Usage: /fusion [list | create <name> <base> <vision> | delete <name> |\n"
    "                enable <name> | disable <name>]\n\n"
    "A fusion model gives a text-only reasoning model the ability to see\n"
    "images, by sending any image to a second, vision-capable model and\n"
    "passing its description to the base model. Pick it with /model <name>.\n\n"
    "<base> and <vision> are <provider>:<model> selectors.\n\n"
    "Example:\n"
    "  /fusion create deepseek-v4-pro-V deepseek:deepseek-v4-pro \\\n"
    "          openrouter:google/gemini-2.5-flash\n"
    "  /model deepseek-v4-pro-V"
)


def _list_text() -> str:
    from src.providers.fusion_models import load_fusion_models

    models = load_fusion_models()
    if not models:
        return (
            "No fusion models saved.\n\n"
            "A fusion model lets a text-only model (deepseek-v4-pro, for one)\n"
            "work with screenshots and images by borrowing vision from another\n"
            "model. Create one with:\n"
            "  /fusion create <name> <provider:model> <provider:model>\n"
            "See /fusion help for an example."
        )
    lines = [f"Fusion models ({len(models)}):"]
    lines.extend(f"  {m.summary()}" for m in models)
    lines.append("")
    lines.append("Select one with /model <name>.")
    return "\n".join(lines)


def _create(rest: list[str]) -> str:
    from src.providers.fusion_models import (
        FusionModelError,
        create_fusion_model,
        suggest_fusion_name,
    )

    if len(rest) == 2:
        # Name omitted — derive it from the base model, following CCR's
        # "use names that make the capability source obvious" convention
        # (GLM-5.2 + GLM-5V-Turbo = GLM-5.2V). Saves the common case from
        # having to invent a name.
        from src.providers.fusion_models import parse_selector

        try:
            base_ref = parse_selector(rest[0], field="base")
        except FusionModelError as exc:
            return str(exc)
        name, base, vision = suggest_fusion_name(base_ref.model), rest[0], rest[1]
    elif len(rest) == 3:
        name, base, vision = rest[0], rest[1], rest[2]
    else:
        return (
            "Usage: /fusion create [<name>] <base> <vision>\n\n"
            "  <base>   the reasoning model, as <provider>:<model>\n"
            "  <vision> a vision-capable model, as <provider>:<model>\n\n"
            "Omit <name> to have one derived from the base model.\n"
            "Example: /fusion create deepseek:deepseek-v4-pro "
            "openrouter:google/gemini-2.5-flash"
        )

    try:
        model = create_fusion_model(name, base, vision)
    except FusionModelError as exc:
        return str(exc)

    return (
        f"Created fusion model {model.name!r}:\n"
        f"  reasoning  {model.base.selector}\n"
        f"  vision     {model.vision.selector}\n\n"
        f"Select it with /model {model.name}. Images will be described by "
        f"{model.vision.selector} before {model.base.model} sees them."
    )


def _delete(rest: list[str]) -> str:
    from src.providers.fusion_models import FusionModelError, delete_fusion_model

    if len(rest) != 1:
        return "Usage: /fusion delete <name>"
    try:
        removed = delete_fusion_model(rest[0])
    except FusionModelError as exc:
        return str(exc)
    return f"Deleted fusion model {removed.name!r}."


def _set_enabled(rest: list[str], enabled: bool) -> str:
    from src.providers.fusion_models import FusionModelError, set_fusion_model_enabled

    verb = "enable" if enabled else "disable"
    if len(rest) != 1:
        return f"Usage: /fusion {verb} <name>"
    try:
        model = set_fusion_model_enabled(rest[0], enabled)
    except FusionModelError as exc:
        return str(exc)
    state = "enabled" if enabled else "disabled"
    suffix = "" if enabled else " It stays saved and can be re-enabled."
    return f"Fusion model {model.name!r} {state}.{suffix}"


def fusion_command_call(args: str, context: CommandContext) -> LocalCommandResult:
    """Handle /fusion. ``context`` is unused — the store is the config file."""
    # ``split()`` on arbitrary whitespace, so a line continuation or double
    # space between selectors (easy to produce when pasting the example)
    # parses the same as single spaces.
    parts = (args or "").split()
    if not parts:
        return LocalCommandResult(type="text", value=_list_text())

    action, rest = parts[0].lower(), parts[1:]

    if action in ("help", "-h", "--help"):
        return LocalCommandResult(type="text", value=_USAGE)
    if action in ("list", "ls", "status"):
        return LocalCommandResult(type="text", value=_list_text())
    if action in ("create", "add", "new"):
        return LocalCommandResult(type="text", value=_create(rest))
    if action in ("delete", "remove", "rm", "del"):
        return LocalCommandResult(type="text", value=_delete(rest))
    if action == "enable":
        return LocalCommandResult(type="text", value=_set_enabled(rest, True))
    if action == "disable":
        return LocalCommandResult(type="text", value=_set_enabled(rest, False))

    return LocalCommandResult(
        type="text", value=f"Unknown /fusion action {parts[0]!r}.\n\n{_USAGE}"
    )


FUSION_COMMAND = LocalCommand(
    name="fusion",
    description="Give a text-only model vision by fusing it with a multimodal one",
    argument_hint="[list | create <name> <base> <vision> | delete|enable|disable <name>]",
    # Every path is text-in/text-out with no UI surface, so the command
    # works non-interactively. Without registration the docstring's claim that
    # this "works identically in the TUI, -p headless, and the SDK" would be
    # false: only the agent-server control + the TUI's hardcoded SLASHES
    # entry could reach it, leaving headless able to USE --model <fusion>
    # but unable to create, list, or delete one.
    supports_non_interactive=True,
)
FUSION_COMMAND.set_call(fusion_command_call)


__all__ = ["FUSION_COMMAND", "fusion_command_call"]
