"""Memory scope isolation extension.

Provides scope-aware memory prompt building that integrates with
``src.memdir.load_memory_prompts()`` without modifying upstream code.

Usage::

    from clawcodex_ext.memory.scope_aware_prompt import (
        build_scope_aware_memory_prompt,
        VALID_MEMORY_SCOPES,
    )

    prompt = build_scope_aware_memory_prompt(["user", "team"])
"""
