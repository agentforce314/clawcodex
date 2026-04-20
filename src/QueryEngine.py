from __future__ import annotations

from .query_engine import QueryEnginePort
from .runtime import PortRuntime


class QueryEngineRuntime(QueryEnginePort):
    def route(self, prompt: str, limit: int = 5) -> str:
        matches = PortRuntime().route_prompt(prompt, limit=limit)
        lines = ['# Query Engine Route', '', f'Prompt: {prompt}', '']
        if not matches:
            lines.append('No mirrored command/tool matches found.')
            return '\n'.join(lines)
        lines.append('Matches:')
        lines.extend(f'- [{match.kind}] {match.name} ({match.score}) — {match.source_hint}' for match in matches)
        return '\n'.join(lines)


__all__ = ['QueryEnginePort', 'QueryEngineRuntime']
