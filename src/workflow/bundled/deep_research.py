meta = {
    "name": "deep-research",
    "description": "Investigate a question across many sources: fan out web searches, fetch and cross-check the findings, vote on each claim, and return a cited report.",
    "when_to_use": "Run with a research question that needs sources cross-checked against each other. Requires the WebSearch tool.",
    "phases": [
        {"title": "Search", "detail": "Fan out web searches across several angles"},
        {"title": "Verify", "detail": "Cross-check each claim with independent agents"},
        {"title": "Synthesize", "detail": "Write a cited report from surviving claims"},
    ],
}

# Bundled deep-research workflow (port of the upstream /deep-research bundle).
# Each agent uses WebSearch/WebFetch to do the actual I/O; the script only
# coordinates the fan-out, cross-checking, and synthesis.

question = (args if isinstance(args, str) else (args or {}).get("question", "")).strip()
if not question:
    raise ValueError('Provide a research question via args — e.g. /deep-research "what changed in X?"')

ANGLES = [
    "official documentation and primary sources",
    "recent news, changelogs, and release notes",
    "expert analysis, comparisons, and critiques",
    "community discussion and real-world reports",
]

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["claim", "source"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        # A string enum, not a bare boolean: many models (deepseek, glm, …)
        # stringify booleans ("true") and fail strict boolean validation, then
        # retry endlessly. A constrained string is emitted reliably.
        "verdict": {"type": "string", "enum": ["supported", "unsupported", "unclear"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

log(f"Researching: {question}")

# ── Phase 1: fan out searches across angles ──────────────────────────────────
phase("Search")
searches = await parallel([
    agent(
        f'Research the question "{question}" focusing on {angle}. Use web search and fetch the '
        f"most relevant sources. Extract concrete, verifiable claims that answer the question, "
        f"each with the URL it came from. Return the structured object.",
        label=f"search:{angle.split(',')[0][:20]}",
        phase="Search",
        schema=CLAIMS_SCHEMA,
    )
    for angle in ANGLES
])

claims = []
seen = set()
for result in searches:
    if not result:
        continue
    for item in result.get("claims", []):
        key = item.get("claim", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            claims.append(item)

if not claims:
    raise RuntimeError("No claims were gathered — the question may be too narrow or WebSearch is unavailable.")

# Cap the verify fan-out: the Verify phase spawns one agent per claim, so a very
# thorough search model (opus gathered 32) would otherwise launch dozens of
# concurrent agents — slow, costly, and prone to overrunning a flaky endpoint.
# Keep the first N distinct claims (search angles are ordered most-authoritative
# first). Logged, never silent.
MAX_VERIFY_CLAIMS = 10
if len(claims) > MAX_VERIFY_CLAIMS:
    log(f"Gathered {len(claims)} claims; verifying the first {MAX_VERIFY_CLAIMS} to bound the fan-out.")
    claims = claims[:MAX_VERIFY_CLAIMS]
else:
    log(f"Gathered {len(claims)} distinct claims; cross-checking each.")

# ── Phase 2: cross-check every claim independently ───────────────────────────
phase("Verify")
verdicts = await parallel([
    agent(
        f'Fact-check this claim about "{question}":\n\n  "{c["claim"]}"\n\n'
        f"(originally cited from {c['source']}). Use web search to check whether it holds up, "
        f"then return your verdict:\n"
        f'- "supported": the claim is accurate or consistent with what you find. This is the '
        f"DEFAULT for a plausible, on-topic claim you do not find contradicted — do NOT reject "
        f"a claim merely because you could not find a second confirming source.\n"
        f'- "unsupported": ONLY if you find concrete evidence it is contradicted, outdated, '
        f"factually wrong, or fabricated.\n"
        f'- "unclear": you genuinely cannot assess it at all.',
        label="verify",
        phase="Verify",
        schema=VERDICT_SCHEMA,
    )
    for c in claims
])

# Keep claims the cross-check did not refute (supported, and unclear-but-not-wrong),
# so the report covers the breadth of the topic rather than only claims that happened
# to be independently re-confirmed. Refuted ("unsupported") claims are dropped.
survivors = [
    c for c, v in zip(claims, verdicts)
    if v and v.get("verdict") in ("supported", "unclear")
]
log(f"{len(survivors)} of {len(claims)} claims survived cross-checking.")

# ── Phase 3: synthesize a cited report ───────────────────────────────────────
phase("Synthesize")
bullet_lines = "\n".join(f"- {c['claim']} (source: {c['source']})" for c in survivors)
report = await agent(
    f'Write a clear, well-organized report answering: "{question}".\n\n'
    f"You already have everything you need below — do NOT use any tools (no web search, "
    f"no web fetch, no retrieving anything). Write the report DIRECTLY from these "
    f"cross-checked claims, citing the source for each point:\n\n{bullet_lines}\n\n"
    f"Structure it with a short summary followed by the details. Note any open questions.",
    label="synthesize",
    phase="Synthesize",
)

return {
    "question": question,
    "report": report,
    "claims_gathered": len(claims),
    "claims_verified": len(survivors),
}
