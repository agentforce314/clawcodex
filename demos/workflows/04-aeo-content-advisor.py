meta = {
    "name": "aeo-content-advisor",
    "description": "AEO Content Advisor — analyze the AI-answer corpus collected by /aeo-data-scientist to find brand content gaps and generate a strategic content roadmap.",
    "when_to_use": "Run with a brand name after /aeo-data-scientist has collected answers. Computes brand visibility / recommendation rates per platform, identifies the questions where the brand is missing, cross-references the domains AI engines trust, and writes an AEO Strategic Audit Report with concrete content ideas.",
    "phases": [
        {"title": "Load", "detail": "Read the accumulated AI-answer corpus from the local workspace"},
        {"title": "Audit", "detail": "Mention depth, recommendation rate, top authorities, content gaps (pure Python)"},
        {"title": "Report", "detail": "AEO Strategic Audit Report (faithful upstream prompt)"},
        {"title": "Export", "detail": "Write report + audit JSON to the workspace"},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Singula-AI AEO marketing workflow — faithful port of the upstream
# "Agent 4: AEO Content Advisor" (5 nodes) to a
# Claude Code dynamic workflow.
#
#   Upstream node                          → workflow implementation
#   1  Retrive AEO data (py, S3 by job)   → SKIPPED: no upstream S3 — we read agent 3's
#   2  Retrieve AEO - Debug (py)            LOCAL workspace directly (answers/<platform>/*.json),
#   3  Workspace path (py)                  which closes the team.context.job_id seam
#   4  Python script (py, audit builder)  → pure Python port (mention depth, recommend keywords,
#                                            ref blacklist, platform stats, content gaps)
#   5  Create text (gpt-4.1)              → agent (faithful prompt; @Builtin-Today → `date`)
#
# Team-bus seam closed: the original took `team.context.job_id` (published by
# agent 3) and re-downloaded that job's files from the upstream S3. Our agent 3
# port accumulates answers in a persistent per-brand workspace, so this
# workflow just points at the same directory — no job ID, no download.
#
# Input : args = "brand"  OR  { brand, brandLink?, workspace?, outputPath? }
#         workspace defaults to ./demos/aeo-output/aeo-data-scientist/<brand-slug>
#         (must contain answers/<platform>/*.json from /aeo-data-scientist)
# Output: { brand, reportPath, auditPath, overallVisibility, topAuthorities, ... }
# ─────────────────────────────────────────────────────────────────────────────

# ── args normalization ───────────────────────────────────────────────────────
input = args if args is not None else {}
if isinstance(input, str):
    t = input.strip()
    if t.startswith("{"):
        try:
            input = json.loads(t)
        except Exception:
            input = {"brand": t}
    else:
        input = {"brand": t}
brand = str(input.get("brand") or "").strip()
if not brand:
    raise RuntimeError('No brand provided. Pass args like "Cursor" or { brand: "Cursor" } — must match the brand used with /aeo-data-scientist.')


# Canonical slugify — keep IDENTICAL across all marketing workflows: the
# CSV/workspace handoff between pipeline stages depends on matching slugs.
def slugify(s, max=80):
    out = re.sub(r"-+$", "", re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))[:max])
    return out or "item"


brandSlug = slugify(brand, 40)
workspace = re.sub(r"/+$", "", input.get("workspace") or f"./demos/aeo-output/aeo-data-scientist/{brandSlug}")
reportPath = input.get("outputPath") or f"{workspace}/aeo-content-advisor-report.md"
auditPath = f"{workspace}/aeo-content-audit.json"

log(f'Brand: "{brand}"  ·  corpus: {workspace}/answers/  ·  report → {reportPath}')

# ── Schemas ──────────────────────────────────────────────────────────────────
CORPUS_SCHEMA = {
    "type": "object",
    "properties": {
        "runDate": {"type": "string"},   # YYYY-MM-DD
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "question": {"type": "string"},
                    "status": {"type": "string"},
                    "answer": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}, "title": {"type": "string"}},
                            "required": ["url", "title"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["platform", "question", "status", "answer", "sources"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["runDate", "answers"],
    "additionalProperties": False,
}

EXPORT_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
    "required": ["files"],
    "additionalProperties": False,
}


# ── pure-Python port of step-4 (audit builder) ───────────────────────────────
def escapeRegex(s):
    return re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", s)


def domainFromUrl(url):
    if not url:
        return ""
    u = url.strip()
    if not re.match(r"^https?://", u, re.IGNORECASE):
        u = "https://" + u
    m = re.match(r"^https?://([^/?#]+)", u, re.IGNORECASE)
    domain = (m.group(1) if m else "").lower()
    return re.sub(r":\d+$", "", re.sub(r"^www\.", "", domain))


# step-4 analyze_mention_depth: count \b-bounded mentions; "recommended" when
# any endorsement keyword appears in a text that mentions the brand
RECOMMEND_KEYWORDS = ["best", "top", "recommend", "reliable", "superior", "choice", "winner", "excellent"]


def analyzeMentionDepth(text, brandName):
    if not text or not brandName:
        return {"count": 0, "is_recommended": False}
    lower = text.lower()
    mentions = len(re.findall(rf"\b{escapeRegex(brandName.lower())}\b", lower))
    isRecommended = mentions > 0 and any(kw in lower for kw in RECOMMEND_KEYWORDS)
    return {"count": mentions, "is_recommended": isRecommended}


# step-4 extract_detailed_sources (blacklist preserved; domain derived from url
# since our canonical answer files store sources as {url, title})
REF_BLACKLIST = ["policies.google.com", "support.google.com", "blog.google", "youtube.com/ads"]


def extractDetailedSources(sources):
    refs = []
    for s in sources or []:
        domain = domainFromUrl(s.get("url"))
        if domain and domain not in REF_BLACKLIST:
            refs.append({"domain": domain, "title": (s.get("title") or "")[:100], "url": s.get("url") or ""})
    return refs


# step-4 process_json_file equivalent over a canonical corpus entry
def processEntry(entry, brandName):
    brandIntel = analyzeMentionDepth(entry.get("answer"), brandName)
    refList = extractDetailedSources(entry.get("sources"))
    brandInCitations = any(brandName.lower() in json.dumps(ref).lower() for ref in refList)
    return {
        "question": entry.get("question") or "Unknown Question",
        "source": entry.get("platform"),
        "include_brand": brandIntel["count"] > 0,
        "mention_count": brandIntel["count"],
        "is_recommended": brandIntel["is_recommended"],
        "brand_in_citations": brandInCitations,
        "ref_list": refList,
    }


# step-4 calculate_advanced_stats (same output keys and formatting)
def calculateAdvancedStats(allResults):
    platformData = {}
    domainCounter = {}
    questionMap = {}
    for r in allResults:
        p, q = r["source"], r["question"]
        platformData[p] = platformData.get(p) or {"tested": 0, "found": 0, "recommendations": 0, "total_mentions": 0}
        platformData[p]["tested"] += 1
        if r["include_brand"]:
            platformData[p]["found"] += 1
            platformData[p]["total_mentions"] += r["mention_count"]
            if r["is_recommended"]:
                platformData[p]["recommendations"] += 1
        questionMap[q] = questionMap.get(q) or {}
        questionMap[q][p] = r["include_brand"]
        for ref in r["ref_list"]:
            domainCounter[ref["domain"]] = domainCounter.get(ref["domain"], 0) + 1

    def pct(x):
        return f"{x * 100:.1f}%"

    byPlatform = {}
    for p, s in platformData.items():
        byPlatform[p] = {
            "visibility": pct(s["found"] / s["tested"]),
            "recommend_rate": pct(s["recommendations"] / s["found"] if s["found"] > 0 else 0),
            "avg_mentions": round((s["total_mentions"] / s["found"]) * 100) / 100 if s["found"] > 0 else 0,
        }
    completelyMissing, partiallyMissing = [], []
    for q, pStatus in questionMap.items():
        foundIn = [p for p, v in pStatus.items() if v]
        missingIn = [p for p, v in pStatus.items() if not v]
        if not foundIn:
            completelyMissing.append({"question": q, "missing": missingIn})
        elif missingIn:
            partiallyMissing.append({"question": q, "found": foundIn, "missing": missingIn})
    totalTested = sum(s["tested"] for s in platformData.values())
    totalFound = sum(s["found"] for s in platformData.values())
    return {
        "brand_health": {
            "overall_visibility": pct(totalFound / totalTested if totalTested > 0 else 0),
            "top_authorities": [d for d, _ in sorted(domainCounter.items(), key=lambda kv: kv[1], reverse=True)[:10]],
        },
        "platform_deep_dive": byPlatform,
        "content_gaps": {
            "high_priority_missing": completelyMissing,
            "opportunity_count": len(completelyMissing) + len(partiallyMissing),
        },
    }


# ═══ Phase: Load — read corpus + current date ════════════════════════════════
phase("Load")

corpusPrompt = f"""Read ALL json files under {workspace}/answers/*/*.json on the local filesystem (use python3; the answers/<platform>/ dir name is the platform). For each file return one entry:
{{platform, question: result.question, status: <"status" field>, answer: <result.answer, or result.aiGenerated.formattedContent if answer is missing/empty; truncate to 6000 chars>, sources: [{{url, title}}] from result.sources (use "" for a missing title; for source items that are plain strings, treat the string as the url)}}.
Skip unparseable files. Also run `date '+%Y-%m-%d'` and return it as runDate.
Return ONLY the structured object. If there are no files, return {{"runDate": <date>, "answers": []}}."""

corpusOut = await agent(corpusPrompt, label="load-corpus", phase="Load", schema=CORPUS_SCHEMA)
if not corpusOut:
    raise RuntimeError("Corpus loader agent failed.")
corpus = [a for a in (corpusOut.get("answers") or []) if a["status"] == "completed" and a["answer"].strip() and a["question"].strip()]
if not corpus:
    raise RuntimeError(
        f"No completed AI answers found under {workspace}/answers/. "
        f'Run /aeo-data-scientist for brand "{brand}" first (it collects the corpus this advisor analyzes), or pass workspace: "<path>" if the data lives elsewhere.'
    )
log(f"Corpus: {len(corpus)} completed answers across {len(set(a['platform'] for a in corpus))} platform(s)")

# ═══ Phase: Audit — step-4 pure Python ═══════════════════════════════════════
phase("Audit")

allResults = [processEntry(entry, brand) for entry in corpus]
auditSummary = calculateAdvancedStats(allResults)
auditJson = {
    "success": True,
    "brand_name": brand,
    "audit_summary": auditSummary,
    "raw_results": allResults,
}
log(f"Audit: visibility {auditSummary['brand_health']['overall_visibility']} · {len(auditSummary['content_gaps']['high_priority_missing'])} questions with brand fully missing · top authority: {(auditSummary['brand_health']['top_authorities'][0] if auditSummary['brand_health']['top_authorities'] else '(none)')}")

# keep the report prompt bounded: drop per-result ref_list details if huge
reportInput = auditJson
if len(json.dumps(auditJson)) > 150000:
    reportInput = {
        **auditJson,
        "raw_results": [{**r, "ref_list": r["ref_list"][:3]} for r in allResults],
    }
    log("Audit JSON > 150KB — ref lists trimmed to 3 per result for the report prompt")

# ═══ Phase: Report — step-5 prompt, faithful ═════════════════════════════════
phase("Report")

reportPrompt = f"""# AEO Strategy & Content Intelligence - Expert Prompt

## Role
You are a senior AEO (Answer Engine Optimization) Strategist. Your goal is to analyze brand audit data and create a content roadmap that forces AI models (ChatGPT, Google AI, Perplexity) to recognize and recommend {brand}.

## Task
1. Analyze the provided JSON to identify "Brand Gaps" where {brand} is missing or not recommended.
2. Cross-reference the "Top Authorities" (domains AI trust) with the missing questions.
3. For each high-priority missing question, generate a strategic content plan.

## Input Data Breakdown
You will receive a JSON containing:
- `audit_summary`: Brand health, visibility rates, and `top_authorities` (the domains AI currently cites).
- `raw_results`: Detailed question-by-question performance including `is_recommended` and `brand_in_citations`.

## Output Format (Markdown)

# AEO Strategic Audit Report: {brand}

## 1. Brand Visibility Scorecard
- **Overall Visibility:** [Overall % from audit_summary]
- **Platform Performance:** - ChatGPT: [Visibility %] (Avg Mentions: [X])
  - Google AI: [Visibility %] (Avg Mentions: [X])
  - Perplexity: [Visibility %] (Avg Mentions: [X])
- **Recommendation Rate:** [Overall Recommend Rate]% (How often AI actually "endorses" the brand)

## 2. Competitive Citation Intelligence
- **Dominant Authorities:** [List Top 5 domains from top_authorities]
- **Strategic Insight:** [Briefly explain if the brand is missing from these top cited domains and what it means for AEO].

## 3. High-Priority Content Solutions
For each question in `high_priority_missing` (or grouped by topic):

### Topic/Question: [Question Text]
- **The Gap:** [Why is the brand missing? e.g., "AI relies on site X which doesn't list us."]
- **Recommended Content Pieces (3-5 ideas):**
  * **Title:** [SEO Optimized: 50-60 chars]
  * **Description:** [Brief, mentioning brand name and a key USP]
  * **AEO Pivot:** [Explain how this specific piece targets the "Top Authorities" or fills a technical data gap for AI models].

## 4. Immediate Action Items
- [List 3 concrete steps, e.g., "Publish a comparison guide vs Competitor X", "Update technical schema on the site"].

## Instructions:
1. **Be Data-Driven:** Use the exact percentages and counts from the JSON.
2. **Be Specific:** Content titles must include the {brand}.
3. **Focus on "Influence":** Don't just suggest blogs; suggest content that provides the *data points* AI models are currently missing.
4. **Tone:** Professional, analytical, and action-oriented.
5. **Time Accuracy**: Today's date is {corpusOut["runDate"]} , for any context that implies current date/year, you must use the correct date.

JSON Data:
{json.dumps(reportInput, indent=2)}

Output ONLY the report markdown — no preamble."""

reportMd = (await agent(reportPrompt, label="aeo-strategy-report", phase="Report")) or ""
if not reportMd.strip():
    raise RuntimeError("Report agent returned no output.")

# ═══ Phase: Export ═══════════════════════════════════════════════════════════
phase("Export")

# Split across two parallel agents to bound per-prompt payload — the audit
# JSON grows with the corpus across scheduled runs.
exportHeader = "Write this local file using a python3 script (create parent dirs, overwrite). The payload must be written EXACTLY as given. Return the structured object {files: [<ABSOLUTE path written>]}."
exps = await parallel([
    agent(f"""{exportHeader}

WRITE {reportPath} with this markdown content:
{reportMd}""", label="export-report-md", phase="Export", schema=EXPORT_SCHEMA),
    agent(f"""{exportHeader}

WRITE {auditPath} with this JSON content:
{json.dumps(auditJson, indent=2)}""", label="export-audit-json", phase="Export", schema=EXPORT_SCHEMA),
])
files = [f for e in exps if e for f in (e.get("files") or [])]
if not files:
    files.extend([reportPath, auditPath])

log(f"Done. Report: {next((f for f in files if f.endswith('.md')), reportPath)}")
return {
    "brand": brand,
    "reportPath": next((f for f in files if f.endswith(".md")), reportPath),
    "auditPath": next((f for f in files if f.endswith(".json")), auditPath),
    "answersAnalyzed": len(corpus),
    "overallVisibility": auditSummary["brand_health"]["overall_visibility"],
    "platformDeepDive": auditSummary["platform_deep_dive"],
    "topAuthorities": auditSummary["brand_health"]["top_authorities"][:5],
    "highPriorityMissing": len(auditSummary["content_gaps"]["high_priority_missing"]),
    "opportunityCount": auditSummary["content_gaps"]["opportunity_count"],
    "note":
        "Reads the local corpus accumulated by /aeo-data-scientist (replaces the upstream S3 job download — team.context.job_id seam closed). "
        "The audit improves as the corpus grows: schedule /aeo-data-scientist daily, re-run this advisor whenever you want a fresh roadmap.",
}
