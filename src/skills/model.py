from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence


@dataclass
class Skill:
    name: str
    description: str
    content: str = ""
    source: str = ""
    loaded_from: str = "skills"
    user_invocable: bool = True
    disable_model_invocation: bool = False
    content_length: int = 0
    is_hidden: bool = False
    skill_root: Optional[str] = None

    aliases: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: Optional[str] = None
    argument_names: list[str] = field(default_factory=list)
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    model: Optional[str] = None
    context: str = "inline"
    agent: Optional[str] = None
    effort: Optional[str] = None
    paths: Optional[list[str]] = None
    display_name: Optional[str] = None
    has_user_specified_description: bool = False
    base_dir: Optional[str] = None
    markdown_content: str = ""
    progress_message: str = "running"

    get_prompt_for_command: Optional[Callable[[str], str]] = None
    is_enabled_fn: Optional[Callable[[], bool]] = None

    @property
    def type(self) -> str:
        return "prompt"

    @property
    def is_conditional(self) -> bool:
        return bool(self.paths)

    def user_facing_name(self) -> str:
        return self.display_name or self.name

    def get_prompt(self, args: str = "") -> str:
        if self.get_prompt_for_command is not None:
            return self.get_prompt_for_command(args)
        content = self.markdown_content or self.content
        if self.base_dir:
            content = f"Base directory for this skill: {self.base_dir}\n\n{content}"
        if args:
            from .argument_substitution import substitute_arguments
            content = substitute_arguments(
                content,
                args,
                append_if_no_placeholder=True,
                argument_names=self.argument_names,
            )
        return content

    def is_enabled(self) -> bool:
        if self.is_enabled_fn is not None:
            return self.is_enabled_fn()
        return True


@dataclass
class PromptSkill(Skill):
    arg_names: list[str] = field(default_factory=list)
