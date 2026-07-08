meta = {
    "name": "deep-research",
    "description": "Investigate a question across many sources: fan out web searches, fetch and cross-check the findings, vote on each claim, and return a cited report.",
    "when_to_use": "Run with a research question that needs sources cross-checked against each other. Requires the WebSearch tool.",
    "phases": [
        {"title": "Search", "detail": "Fan out web searches across several angles"},
        {"title": "Verify", "detail": "Cross-check each claim with independent agents"},
        {"title": "Synthesize", "detail": "Write a cited report from surviving claims"},
    ],
    # #283: a verbose model burned ~888k tokens in Search+Verify with no
    # ceiling. Applied by the engine when the caller set no budget;
    # CLAWCODEX_DEEP_RESEARCH_TOKEN_BUDGET overrides (0 disables).
    "default_budget": 400000,
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

# Budget-aware degradation (#283): surface the Search spend, and only
# launch as many verifiers as the remaining budget affords, reserving
# headroom for Synthesize. Claims that can't be afforded pass through
# UNVERIFIED rather than being dropped (the cross-check default is
# "supported unless contradicted", and an unrun check contradicts
# nothing). With no budget set, everything is verified as before.
# Note: the estimate gates how many verifiers LAUNCH; spend within an
# already-launched wave is uncapped (the engine's ceiling only trips
# between calls), so the Synthesize step below re-checks the budget and
# falls back to the raw claims if the waves overshot.
SYNTH_RESERVE = 40000
to_verify = claims
unverified = []
if budget.total:
    search_spent = budget.spent()
    log(f"Search spent ~{search_spent:,} of the {budget.total:,}-token budget.")
    per_verifier = max(2000, search_spent // max(1, len(ANGLES)))
    affordable = int(max(0, (budget.remaining() - SYNTH_RESERVE) // per_verifier))
    if affordable < len(claims):
        to_verify = claims[:affordable]
        unverified = claims[affordable:]
        if not to_verify:
            log(
                f"Token budget nearly exhausted ({budget.remaining():,.0f} left); "
                f"skipping cross-check — all {len(claims)} claims pass through unverified."
            )
        else:
            log(
                f"Token budget affords cross-checking {len(to_verify)} of {len(claims)} "
                f"claims (~{per_verifier:,} tokens each); {len(unverified)} pass through unverified."
            )

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
    for c in to_verify
]) if to_verify else []

# Keep claims the cross-check did not refute (supported, and unclear-but-not-wrong),
# so the report covers the breadth of the topic rather than only claims that happened
# to be independently re-confirmed. Refuted ("unsupported") claims are dropped.
# A None verdict (verifier failed or hit the budget ceiling) contradicts
# nothing — the claim passes through unverified instead of vanishing.
survivors = []
failed_checks = 0
for c, v in zip(to_verify, verdicts):
    if v is None:
        failed_checks += 1
        survivors.append(c)
    elif v.get("verdict") in ("supported", "unclear"):
        survivors.append(c)
survivors.extend(unverified)
log(
    f"{len(survivors)} of {len(claims)} claims survived cross-checking"
    + (f" ({failed_checks} verifier(s) did not complete; their claims pass through unverified)" if failed_checks else "")
    + (f" ({len(unverified)} not checked: budget)" if unverified else "")
    + "."
)
if budget.total:
    log(f"Spend before Synthesize: ~{budget.spent():,} of {budget.total:,} tokens.")

# ── Phase 3: synthesize a cited report ───────────────────────────────────────
phase("Synthesize")
bullet_lines = "\n".join(f"- {c['claim']} (source: {c['source']})" for c in survivors)
# Re-check the ceiling before the final call (#283): a verbose model can
# overshoot the estimate WITHIN the already-launched search/verify waves,
# and a tripped ceiling here would otherwise fail the whole run with no
# report after spending the entire budget. Better an un-synthesized
# claim list than expensive nothing.
if budget.total and budget.spent() >= budget.total:
    log(
        f"Budget exhausted before Synthesize (~{budget.spent():,} of {budget.total:,}); "
        f"returning the raw surviving claims instead of a synthesized report."
    )
    report = (
        "(Token budget exhausted before synthesis — raw cross-checked claims:)\n\n"
        + bullet_lines
    )
else:
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
