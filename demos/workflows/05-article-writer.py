meta = {
    "name": "article-writer",
    "description": "Article Writing Team — write an AEO-optimized blog article from a title + your product URL, then refine it into a natural, engaging post (two-pass: draft → polish).",
    "when_to_use": "Run with a blog article title and a product URL (plus an optional content guide) to produce a publish-ready markdown article that weaves the product context in subtly. Pairs with /aeo-content-advisor: feed it the content ideas/titles from the advisor report.",
    "phases": [
        {"title": "Context", "detail": "Fetch the product URL and extract product context"},
        {"title": "Draft", "detail": "Write the full-length blog article (faithful upstream prompt)"},
        {"title": "Polish", "detail": "Editorial refinement pass (faithful upstream prompt)"},
        {"title": "Export", "detail": "Save the article as markdown"},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Singula-AI AEO marketing workflow — faithful port of the upstream
# "Agent 5: Article Writing Team Version" (3 nodes)
# to a Claude Code dynamic workflow.
#
#   Upstream node                          → workflow implementation
#   1  Product context (fetch_web)        → agent + WebFetch (WebSearch fallback) — the
#                                            original drove a Playwright Chrome page
#   2  Blog generator (gpt-5.1)           → agent (faithful prompt; @Builtin-Today → `date`)
#   3  Improve writing (gpt-5.1)          → agent (faithful prompt; one genericization:
#                                            "lifestyle or fitness blogger" was a client
#                                            leftover → blogger in the product's niche,
#                                            overridable via args.tone)
#   —  Output html (medium-article-template) → markdown file (the HTML render was a
#                                            upstream app template; .md is the portable artifact)
#
# Team-bus seam: team.input.productLink → args.productUrl.
#
# Input : args = { productUrl (required), title (required), contentGuide?, tone?, outputDir? }
#         (bare string starting with http → productUrl; you'll be asked for a title)
# Output: { articlePath, title, productUrl, words }
# ─────────────────────────────────────────────────────────────────────────────

# ── args normalization ───────────────────────────────────────────────────────
data = args if args is not None else {}
if isinstance(data, str):
    t = data.strip()
    if t.startswith("{"):
        try:
            data = json.loads(t)
        except Exception:
            data = {}
    elif re.match(r"^https?://", t, re.IGNORECASE):
        data = {"productUrl": t}
    else:
        data = {"title": t}
product_url = str(data.get("productUrl") or data.get("url") or "").strip()
title = str(data.get("title") or "").strip()
content_guide = str(data.get("contentGuide") or data.get("guide") or "").strip()
tone = str(data.get("tone") or "").strip()
if not product_url or not title:
    raise RuntimeError(
        "Need both a product URL and an article title. Pass args like "
        '{ productUrl: "https://cursor.com", title: "Why AI Coding Assistants Beat Autocomplete", contentGuide?: "...", tone?: "..." }. '
        'Tip: take titles from the /aeo-content-advisor report ("Recommended Content Pieces").'
    )


# Canonical slugify — keep IDENTICAL across all marketing workflows.
def slugify(s, max=80):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())
    s = re.sub(r"^-+|-+$", "", s)
    s = s[:max]
    s = re.sub(r"-+$", "", s)
    return s or "item"


output_dir = re.sub(r"/+$", "", str(data.get("outputDir") or "./demos/aeo-output/articles"))
article_path = f"{output_dir}/{slugify(title)}.md"

log(f'Article: "{title}"  ·  product: {product_url}  ·  output: {article_path}')

# ── Schemas ──────────────────────────────────────────────────────────────────
CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "today": {"type": "string"},          # YYYY-MM-DD
        "productContext": {"type": "string"},  # extracted page content
        "fetchNote": {"type": "string"},        # '' or how the fallback was used
    },
    "required": ["today", "productContext", "fetchNote"],
    "additionalProperties": False,
}

EXPORT_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
    "required": ["files"],
    "additionalProperties": False,
}

# ═══ Step 1: Product context (fetch_web → WebFetch) ══════════════════════════
phase("Context")

context_prompt = f"""You are extracting product context from a company's website for a content writer.

1. Run `date '+%Y-%m-%d'` and return it as today.
2. Fetch {product_url} with WebFetch and extract the page's substantive content as plain text: what the product is, who it's for, key features and capabilities, pricing signals, positioning/tagline, and any notable proof points (customers, numbers). Preserve concrete facts verbatim where possible; skip navigation/footer boilerplate. Aim for 300-800 words of faithful extraction — this stands in for the raw page content, so do not editorialize or add outside knowledge.
3. If WebFetch fails or returns no useful content (JS-only page, block page), use WebSearch for the product/site and reconstruct the same context from search results — and say so in fetchNote (e.g. "WebFetch blocked; built from search results"). Otherwise fetchNote is "".

Return ONLY the structured object {{today, productContext, fetchNote}}."""

ctx = await agent(context_prompt, label="product-context", phase="Context", schema=CONTEXT_SCHEMA)
if not ctx or not ctx["productContext"].strip():
    raise RuntimeError(f"Could not extract product context from {product_url}.")
if ctx["fetchNote"]:
    log(f"Product context note: {ctx['fetchNote']}")

# ═══ Step 2: Blog generator (faithful prompt) ════════════════════════════════
phase("Draft")

draft_prompt = f"""You are a skilled blog writer and content strategist. Write a full-length blog article in Markdown format based on the following inputs:

Product Context (from Product URL extraction):
{ctx["productContext"]}

Blog article title:
{title}

Content Guide: {content_guide or "(empty)"}
(Note: This field may be empty. If provided, follow the user's guide closely, but do not break the required Markdown formatting rules.)

Today's date is: {ctx["today"]}

Writing Guidelines:
1. Analyze all the Topic options and choose the strongest perspective to guide the article. You may blend elements of multiple Topics if it strengthens the narrative.
2. Write in an engaging blog style — conversational yet professional, with smooth transitions, storytelling elements, and practical insights.
3. Use Markdown headings (##, ###) for clear structure.
4. Start with a strong **hook in the introduction** that makes readers want to keep reading.
5. Include examples, metaphors, or short anecdotes to make abstract ideas relatable.
6. Naturally integrate both Original + Researched Keywords for SEO, but never make it feel forced.
7. Weave the Product Context into the narrative subtly — highlight relevance without hard-selling.
8. Break down the body into scannable sections (Executive Summary, Introduction, Market Insights, Product Relevance, Actionable Tips, Conclusion).
9. End with a clear **conclusion and call-to-action** aligned with the Blog Intent.
10. Keep the tone aligned with the Content Guide (authoritative, friendly, inspirational, technical, etc.).
11. If any context in title and body implies current time, you must use the correct date/year as of today: {ctx["today"]}

Output:
Return only the final blog article in **Markdown format**, with headings, subheadings, bullet points, and short paragraphs for readability.
Do not wrap the output in ```markdown code block markers"""

draft = (await agent(draft_prompt, label="blog-generator", phase="Draft")) or ""
if not draft.strip():
    raise RuntimeError("Blog generator returned no output.")
draft_words = len(re.split(r"\s+", draft))
log(f"Draft: ~{draft_words} words")

# ═══ Step 3: Improve writing (faithful prompt; persona genericized) ══════════
phase("Polish")

persona_line = (
    f"Make the tone conversational and relatable, matching this voice: {tone}."
    if tone
    else "Make the tone conversational and relatable, as if written by an experienced blogger in this product's niche."
)

polish_prompt = f"""You are a professional blog editor. Take the draft blog article below and refine it into a natural, engaging blog post while keeping all the essential information intact.

Draft Blog Article:
{draft}

Refinement Guidelines:
1. {persona_line}
2. Keep the Markdown structure (headings, lists, formatting) but smooth out stiff or report-like phrasing.
3. Add storytelling elements, anecdotes, or examples that make the article feel more personal and vivid.
4. Replace technical specs or numbers with reader-friendly comparisons or metaphors (unless specs are critical).
5. Weave in a sense of personality: rhetorical questions, casual expressions, light humor, or emojis where appropriate.
6. Ensure flow between sections feels natural, with smooth transitions rather than abrupt bullet points.
7. Preserve SEO keywords, but integrate them subtly so they feel organic.
8. Always end with a warm, motivating conclusion and a call-to-action that feels human and encouraging.

Output:
Return only the refined blog article in **Markdown format**, keeping headings and formatting consistent."""

article = (await agent(polish_prompt, label="improve-writing", phase="Polish")) or ""
if not article.strip():
    raise RuntimeError("Improve-writing agent returned no output.")

# ═══ Export ══════════════════════════════════════════════════════════════════
phase("Export")

export_prompt = f"""Write this local file using a python3 script (create parent dirs, overwrite). Return the structured object {{files: [<absolute path written>]}}.

WRITE {article_path} with EXACTLY this markdown content:
{article}"""

exp = await agent(export_prompt, label="export-article", phase="Export", schema=EXPORT_SCHEMA)
final_path = next((f for f in ((exp and exp.get("files")) or []) if f.endswith(".md")), article_path)

log(f"Done. Article: {final_path}")
return {
    "title": title,
    "productUrl": product_url,
    "articlePath": final_path,
    "words": len(re.split(r"\s+", article)),
    "contextNote": ctx["fetchNote"] or "product context fetched from URL",
    "note": (
        "Two-pass output (draft → editorial polish), faithful to the upstream agent. "
        "Deviation: saved as markdown instead of the upstream medium-article-template HTML render; "
        "the step-3 \"lifestyle or fitness blogger\" persona (a client leftover) is genericized to the product's niche — override with args.tone."
    ),
}
