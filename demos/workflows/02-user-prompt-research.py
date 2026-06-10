meta = {
    "name": "user-prompt-research",
    "description": "AEO User Prompt Research — discover what customers really ask about a company/product/keyword, score the questions by search volume, and export to CSV.",
    "when_to_use": "Run with a company name, product name, or search keyword to mine real user questions and intent for Answer Engine Optimization (AEO). Produces a Time/Question/Search Volume CSV.",
    "phases": [
        {"title": "Search", "detail": "Reddit + Google search (parallel) for the keyword"},
        {"title": "Research", "detail": "Keyword research doc + N condensed intent signals"},
        {"title": "Questions", "detail": "Rewrite intent signals into natural user questions"},
        {"title": "Keywords", "detail": "Extract core/semantic/longtail keywords per question"},
        {"title": "Volume", "detail": "Estimate monthly search volume for semantic keywords"},
        {"title": "Export", "detail": "Compute per-question volume and write the CSV"},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Singula-AI AEO marketing workflow — faithful port of the upstream
# "Agent 2: User Prompt research V2" (12 nodes) to
# a Claude Code dynamic workflow.
#
#   Upstream node                          → workflow implementation
#   1  Search reddit (search_reddit)      → agent + WebSearch (site:reddit.com)
#   2  Search Google API (SerpAPI)        → agent + WebSearch (schema: organic + PAA)
#   3  Google search results (py)         → pure Python formatter (format_organic)
#   4  People also search (py)            → pure Python formatter (format_people_also_ask)
#   5  Keyword Research Docs (gpt-4.1)    → agent + WebSearch / WebFetch(guide URL)
#   6  Asking questions (gpt-4.1-mini)    → agent
#   7  Question -> Keywords (gpt-4.1)     → agent (schema: per-question keywords)
#   8  Combine Question Keywords (py)     → pure Python (combine_semantic)
#   9  Fetch Keywords Search Volume (py)  → agent estimate (DataForSEO unavailable)
#   10 Calc Question Search Volume (py)   → pure Python (calc_question_volume)
#   11 Export questions (py, writes CSV)  → agent + Write / Bash (merge + write CSV)
#   12 Csv file path (js)                 → workflow return value
#
# Input : args = "keyword"  OR  { keyword|productKeyword|topic, count?, guideUrl?, outputDir? }
# Output: { csvPath, keyword, questionsCount, ... }
# ─────────────────────────────────────────────────────────────────────────────

# args may arrive as an object, a bare keyword string, or — depending on how the
# command is invoked — a JSON-encoded string. Normalize all three.
input = args if args is not None else {}
if isinstance(input, str):
    t = input.strip()
    if t.startswith("{") or t.startswith("["):
        try:
            input = json.loads(t)
        except Exception:
            pass  # treat as a plain keyword
keyword = (
    input
    if isinstance(input, str)
    else (input.get("keyword") or input.get("productKeyword") or input.get("topic") or "")
)
keyword = str(keyword).strip()
if not keyword:
    raise ValueError(
        'No keyword provided. Pass a company name, product name, or search keyword via args — '
        'e.g. args: "AI coding assistant"  or  args: { keyword: "AI coding assistant", count: 50 }'
    )
count = int(input["count"]) if (isinstance(input, dict) and input.get("count")) else 50
# The upstream original fetched its Keyword Research Guide from a
# third-party CDN on every run — a third-party remote-instruction
# dependency seed data must not ship. The guide content is VENDORED below
# instead (fetched verbatim 2026-06-09; human-readable copy:
# keyword-research-guide.md next to this file in the seed folder).
# Pass guideUrl explicitly to fetch a different guide at runtime.
guide_url = input["guideUrl"] if (isinstance(input, dict) and input.get("guideUrl")) else ""

KEYWORD_RESEARCH_GUIDE = """Purpose of this step:
Transform keyword research signals into fully-formed, natural user questions
that reflect real decision-making contexts, not short search queries.

The goal is to generate questions that a real person would ask an AI assistant
when trying to understand, evaluate, or choose a solution — similar in depth
and intent to questions used in conversational AI systems (e.g. Rufus-style queries).

Critical Guideline:
Do NOT generate short keyword-style queries.
Every output must be a complete, natural-language question that could be spoken aloud.

A question is invalid if it resembles a search keyword rather than a real inquiry.

---

How to generate questions:

1. Start from intent, not modifiers

Instead of expanding keywords using patterns like:
- best X
- X vs Y
- X for beginners

Infer the underlying situation:
- Why is the user asking this?
- What decision are they trying to make?
- What uncertainty are they trying to resolve?

Then express that as a full question.

Bad example:
"best indoor exercise bike"

Good example:
"What should someone look for when choosing an indoor exercise bike for regular home workouts?"

---

2. Embed usage context or decision pressure

Strong questions usually include at least one of:
- a use case (home, apartment, family, beginners, heavy use, rehab, etc.)
- a constraint (space, budget sensitivity, noise, durability, learning curve)
- a decision stage (first-time buyer, upgrading, switching, comparing options)

Bad example:
"exercise bike comparison"

Good example:
"When comparing indoor exercise bikes for a small apartment, what differences actually matter in daily use?"

---

3. Favor reasoning-oriented questions over lookup questions

Avoid questions that can be answered with a definition or a short list.

Prefer questions that require:
- explanation
- comparison
- trade-off analysis
- examples from real products or brands

Bad example:
"what is project management software"

Good example:
"How do teams typically use project management software differently as they grow from a few people to a larger organization?"

---

4. Allow brand recall to happen naturally, but do not force it

Do not include brand names in the questions.

However, questions should be framed in a way that makes brand examples
a natural and helpful part of the answer.

Example:
Instead of:
"X vs Y software comparison"

Use:
"When people compare popular tools in this category, what differences usually stand out in real-world use?"

---

5. Vary question types across the set

Across the full output, include a mix of:
- Category understanding questions
- Comparison and evaluation questions
- Alternative and substitution questions
- Use-case driven selection questions
- Long-term ownership or experience questions
- Market or ecosystem understanding questions

Avoid repeating the same structure or phrasing.

---

Internal quality check (do not output):
For each question, ask:
"Does this sound like something a real person would ask an AI, not a search engine?"
If not, rewrite it.

---

Output format:
Return only the generated questions.
One question per line.
No bullet points, no numbering, no extra explanation."""
output_dir = (
    input["outputDir"]
    if (isinstance(input, dict) and input.get("outputDir"))
    else "./demos/aeo-output/user-prompt-research"
)


# Canonical slugify — keep IDENTICAL across all marketing workflows: the
# CSV/workspace handoff between pipeline stages depends on matching slugs.
def slugify(s, max=80):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())
    s = re.sub(r"^-+|-+$", "", s)
    s = s[:max]
    return re.sub(r"-+$", "", s) or "item"


slug = slugify(keyword)
csv_path = f"{output_dir}/{slug}-questions.csv"

log(f'Keyword: "{keyword}"  ·  target signals/questions: {count}  ·  output: {csv_path}')

# ── JSON Schemas for the structured (schema-forced) agent steps ──────────────
GOOGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "organic_results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "snippet": {"type": "string"},
                    "link": {"type": "string"},
                },
                "required": ["title", "snippet", "link"],
                "additionalProperties": False,
            },
        },
        "related_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["organic_results", "related_questions"],
    "additionalProperties": False,
}

KEYWORDS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "core": {"type": "string"},
                    "semantic": {"type": "array", "items": {"type": "string"}},
                    "longtail": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "core", "semantic", "longtail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

VOLUME_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "search_volume": {"type": "integer"},
                },
                "required": ["keyword", "search_volume"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


# ── Pure-Python ports of the upstream python transforms (steps 3, 4, 8, 10) ───
def format_organic(organic):
    if not organic:
        return "No organic results found."
    lines = ["=== Search Results ===\n"]
    for i, item in enumerate(organic):
        lines.append(f"[{i + 1}] {item.get('title') or ''}")
        lines.append(f"Source: {item.get('link') or ''}")
        lines.append(f"{item.get('snippet') or ''}\n")
    return "\n".join(lines)


def format_people_also_ask(questions):
    if not questions:
        return "No 'People also ask' questions found."
    lines = ["=== People Also Search ===\n"]
    for i, q in enumerate(questions):
        lines.append(f"Q{i + 1}: {q.get('question') or ''}")
    return "\n".join(lines)


def combine_semantic(items):
    s = set()
    for it in items:
        for kw in (it.get("semantic") or []):
            if kw and kw.strip():
                s.add(kw.strip())
    return sorted(s)


# Port of step-10 script.py: weighted volume of the top-3 semantic keywords
# per question (weights 0.5/0.3/0.2, normalized); < 50 → "10~50".
def calc_question_volume(items, volume_items):
    vol_map = {}
    for v in volume_items:
        if v and v.get("keyword"):
            vol_map[v["keyword"].lower()] = v["search_volume"]
    weights = [0.5, 0.3, 0.2]
    results = {}
    for content in items:
        question = content.get("question") or ""
        semantic = content.get("semantic") or []
        found = []
        missing = []
        for kw in semantic:
            key = (kw or "").lower()
            vol = vol_map.get(key)
            if vol is not None:
                found.append([kw, vol])
            else:
                missing.append(kw)
        found.sort(key=lambda a: a[1], reverse=True)
        top = found[:3]
        sum_w = sum(weights[: len(top)])
        if sum_w == 0:
            sum_w = 1
        weighted = 0
        for idx, t in enumerate(top):
            weighted += t[1] * weights[idx]
        final_vol = weighted / sum_w
        results[question] = {
            "top_semantic_keywords": [t[0] for t in top],
            "search_volume": "10~50" if final_vol < 50 else round(final_vol),
            "missing_semantic_keywords": missing,
        }
    return results


# ═══ Steps 1 + 2 : Reddit + Google search (parallel) ═════════════════════════
phase("Search")

reddit_prompt = f"""You are researching what real users say about a topic on Reddit, for SEO/AEO keyword research.

Topic / search keywords: "{keyword}"

Use web search (try queries like: site:reddit.com {keyword}  ·  {keyword} reddit  ·  "{keyword}" reddit recommendations / experiences / vs / alternatives) to find the most relevant Reddit threads and discussions.

Return a concise PLAIN-TEXT digest of the most relevant Reddit findings (aim for 10-20 threads). For each thread: put the subreddit + thread title on one line, then a 1-3 sentence summary of what users are asking, recommending, complaining about, comparing, or deciding between. Capture real user language, pain points, trade-offs, constraints, and comparison moments. No preamble and no markdown headers."""

google_prompt = f"""You are gathering Google search results for SEO/AEO research — simulate scraping the Google SERP for this query.

Query: "{keyword}"

Use web search to gather:
1) organic_results: the top ~10 organic web results. For each: title, snippet (a 1-2 sentence description of the page — REMOVE any raw URLs from the snippet text), and link (the result URL).
2) related_questions: the "People also ask" style questions related to this query (aim for 8-10). Each item is just the question text.

Return ONLY the structured object."""

search_results = await parallel([
    lambda: agent(reddit_prompt, label="reddit-search", phase="Search"),
    lambda: agent(google_prompt, label="google-search", phase="Search", schema=GOOGLE_SCHEMA),
])
reddit = search_results[0] or "No Reddit results available."
google = search_results[1] or {"organic_results": [], "related_questions": []}

# ═══ Steps 3 + 4 : format the SERP (pure Python) ═════════════════════════════
google_results_text = format_organic(google.get("organic_results"))
people_also_ask_text = format_people_also_ask(google.get("related_questions"))
log(
    f"Google: {len(google.get('organic_results') or [])} organic results, "
    f"{len(google.get('related_questions') or [])} related questions"
)

# ═══ Step 5 : Keyword Research Docs ══════════════════════════════════════════
phase("Research")

if guide_url:
    _guide_block = (
        f"- Keyword Research Guide: {guide_url}  (fetch this with WebFetch; if it is unavailable, "
        f"fall back to standard SEO/AEO keyword-research best practices)"
    )
else:
    _guide_block = f"""- Keyword Research Guide (apply this methodology):
<keyword_research_guide>
{KEYWORD_RESEARCH_GUIDE}
</keyword_research_guide>
  (Note: the guide's "Output format" section describes question generation in a later step — for THIS task, follow the Task and output requirements stated below.)"""

research_prompt = f"""Input:
{_guide_block}
- Original search keywords: [ {keyword} ]
- Google search results:
{google_results_text}
- Google "people also search":
{people_also_ask_text}
- Reddit search results:
{reddit}
- In addition, use web search as much as possible to obtain more relevant information.
- Today: determine the current date (run `date +%Y-%m-%d` if helpful).

Task:
Using the Keyword Research Guide, analyze the topic and search results.

In addition to identifying keywords and opportunities, focus on understanding and articulating:
- The typical situations users are in when searching this topic
- The decisions they are trying to make
- The uncertainties, trade-offs, or constraints they are facing
- Common comparison or evaluation moments observed in search and discussions

Do NOT compress everything into keyword phrases.
Any section written primarily as search-style phrases rather than descriptive language should be considered invalid and rewritten.
Instead, explain these findings in clear natural language, as short descriptive bullet points or short paragraphs, preserving user context and reasoning.

At the end, include a section called: "Condensed Intent Signals"
- This section should list short keyword-style phrases (one per line) that represent the compressed form of the above contexts.
- In the "Condensed Intent Signals" section, produce exactly {count} distinct intent signals, one per line."""

research_doc = await agent(research_prompt, label="keyword-research-doc", phase="Research")
if not research_doc:
    raise RuntimeError("Step 5 (Keyword Research Docs) returned no output.")

# ═══ Step 6 : Asking questions ═══════════════════════════════════════════════
phase("Questions")

questions_prompt = f"""You are rewriting condensed intent signals into natural, user-facing questions.

Context:
The intent signals are shorthand labels derived from deeper research.
They are not final questions and must be expressed as real queries.

Inputs:
1) Full research context (the "Condensed Intent Signals" section is at the end):
{research_doc}

Task:
For each intent signal, rewrite it as a natural question that represents
a selection or filtering intent — describing what kind of solution
the user is looking for.

Rules:
- Each output must be a complete question ending with a question mark.
- The question should be concise and search-like, but sound natural when spoken.
- Do NOT explain reasoning, trade-offs, or decision logic.
- Do NOT ask how to choose, evaluate, or consider options.
- Do NOT output keyword-style fragments or reuse the intent signal verbatim.
- Do NOT introduce brand names unless they appear in the input.

Quality check (internal):
If the question sounds like a buying guide, expert advice request,
or long-form explanation prompt, shorten or simplify it.

Output:
One question per line. No numbering. No bullets. No extra text. Produce {count} questions."""

questions_raw = await agent(questions_prompt, label="asking-questions", phase="Questions")
question_lines = [
    line
    for line in (
        re.sub(r"^\s*[-*\d.)\]]+\s*", "", s).strip()
        for s in (questions_raw or "").split("\n")
    )
    if len(line) > 0 and "?" in line
]
if not question_lines:
    raise RuntimeError("Step 6 (Asking questions) produced no questions.")
log(f"Generated {len(question_lines)} questions")

# ═══ Step 7 : Question -> Keywords ═══════════════════════════════════════════
phase("Keywords")

keywords_prompt = f"""You are an SEO expert. Extract keywords from each of the following questions.

Questions (one per line):
{chr(10).join(question_lines)}

Rules:
1. Identify ONE core intent keyword.
2. Identify up to 5 semantic-equivalent keywords (same user intent).
3. Identify up to 5 long-tail supporting keywords.
4. Remove stopwords.
5. Do NOT invent brands.

Return one object per question. Each object must include the EXACT original question text (verbatim) plus its core, semantic (array), and longtail (array) keywords."""

kw_out = await agent(keywords_prompt, label="question-to-keywords", phase="Keywords", schema=KEYWORDS_SCHEMA)
kw_items = (kw_out and kw_out.get("items")) or []
if not kw_items:
    raise RuntimeError("Step 7 (Question -> Keywords) produced no keyword items.")

# ═══ Step 8 : Combine semantic keywords (pure Python) ════════════════════════
semantic_list = combine_semantic(kw_items)
log(f"Combined {len(semantic_list)} unique semantic keywords across {len(kw_items)} questions")

# ═══ Step 9 : Fetch Keywords Search Volume (estimated) ═══════════════════════
phase("Volume")

volume_prompt = f"""You are an SEO keyword research analyst. Estimate the approximate average MONTHLY Google search volume (US market) for each keyword below.

Keywords (one per line):
{chr(10).join(semantic_list)}

Notes:
- Live DataForSEO / Google Ads data is not available here, so produce reasoned estimates based on your knowledge and (optionally) web-search signals about popularity and competition.
- search_volume must be an integer (average monthly searches). Use 0 only when there is essentially no search demand.

Return one object per keyword: keyword (exact text as given) and search_volume (integer)."""

vol_out = await agent(volume_prompt, label="search-volume", phase="Volume", schema=VOLUME_SCHEMA)
vol_items = (vol_out and vol_out.get("items")) or []
log(f"Estimated search volume for {len(vol_items)} keywords")

# ═══ Step 10 : Calc per-question search volume (pure Python) ══════════════════
question_volumes = calc_question_volume(kw_items, vol_items)
vol_by_q = {}
for q, info in question_volumes.items():
    vol_by_q[q.strip().lower()] = info["search_volume"]
rows = [[q, vol_by_q.get(q.strip().lower(), 0)] for q in question_lines]

# ═══ Step 11 : Export questions to CSV (agent writes the file) ═══════════════
phase("Export")

export_prompt = f"""Write research questions to a CSV file, MERGING with any existing file at the same path. Follow this exactly.

Output CSV path (relative to the current working directory): {csv_path}

CSV columns (exact header line): Time,Question,Search Volume

New rows to add — JSON array of [question, search_volume] pairs:
{json.dumps(rows, indent=2)}

Rules (follow precisely):
1. "Time" is the current timestamp formatted "YYYY-MM-DD HH:MM" — get it once via:  date '+%Y-%m-%d %H:%M'  — and use that same value for every NEW row you add.
2. Ensure the parent directory of the output path exists (create it if needed).
3. If the CSV already exists, read it and merge:
   - Keep all existing rows.
   - For a question that already exists, UPDATE its Search Volume only if the new value is non-empty, not "0", and different from the existing value (keep the existing Time for those).
   - Add genuinely new questions as new rows with the current timestamp.
4. Properly CSV-quote any field containing a comma, double-quote, or newline.
5. Sort all rows by Time, then by Question.
6. Write the complete file: the header line followed by all rows.

Implement this by writing a small Python 3 script to a temp file and running it with python3 (do NOT rely on any pre-injected variables). After the file is written, print ONLY the absolute path to the CSV file as the final line of your output — nothing else."""

export_out = await agent(export_prompt, label="export-csv", phase="Export")
out_lines = [s.strip() for s in (export_out or "").split("\n") if s.strip()]
final_path = next((line for line in reversed(out_lines) if line.endswith(".csv")), csv_path)

# ═══ Step 12 : CSV file path (workflow return value) ═════════════════════════
log(f"Done. CSV written to: {final_path}")
return {
    "keyword": keyword,
    "csvPath": final_path,
    "questionsCount": len(question_lines),
    "semanticKeywordsCount": len(semantic_list),
    "intentSignalsTarget": count,
    "organicResults": len(google.get("organic_results") or []),
    "relatedQuestions": len(google.get("related_questions") or []),
    "note": "Search volumes are model-estimated (DataForSEO / Google Ads not available in Claude Code). Reddit + Google SERP gathered via WebSearch instead of the upstream SerpAPI / Reddit integrations.",
}
