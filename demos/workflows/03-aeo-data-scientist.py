meta = {
    "name": "aeo-data-scientist",
    "description": "AEO Full-Stack Data Scientist — ask research questions on ChatGPT, Google AI and Perplexity (cookie-less browser), measure brand visibility vs competitors, analyze cited domains, and write an AEO report.",
    "when_to_use": "Run with a brand name plus research questions (or the CSV produced by /user-prompt-research) to measure how often the brand appears in AI-engine answers, who the competitors are, and which domains get cited. Designed for small daily batches: each run scrapes a few uncovered questions and recomputes scores/report over ALL accumulated data.",
    "phases": [
        {"title": "Init", "detail": "Load questions (args or CSV) + scan workspace coverage"},
        {"title": "Scrape", "detail": "Ask each pending question on ChatGPT / Google AI / Perplexity, no login"},
        {"title": "Competitors", "detail": "Extract + clean competitor brands from AI answers"},
        {"title": "Visibility", "detail": "Brand/competitor visibility scores (pure port)"},
        {"title": "Domains", "detail": "Citation domain matrix, URL mapping, LLM categorization"},
        {"title": "Report", "detail": "Write the AEO report + run log"},
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Singula-AI AEO marketing workflow — faithful port of the upstream
# "Agent 3: AEO Full-Stack Data Scientist" (27 nodes)
# to a Claude Code dynamic workflow.
#
#   Upstream node                              → workflow implementation
#   1  Generate job id (py)                   → init agent (date-based id; no RNG in script)
#   2  Workspace path (py, /tmp/<job>)        → persistent ./demos/aeo-output/aeo-data-scientist/<brand-slug>/
#   3  Initialization (py, metadata.json)     → run log appended to metadata.json (export agent)
#   4  Question list (py passthrough)         → args.questions OR /user-prompt-research CSV (closes the
#                                               team-bus seam the upstream left manual)
#   5  Extension scraping (chrome_extension)  → per-question agent + gstack /browse, NO COOKIES
#   6  Save Extension Result (py)             → same agent writes canonical answer JSONs
#   7  1-Gather AI results (loop, Playwright) → same agents (sequential loop, small batch per run)
#   8  Select Data Retrieval Mode (js)        → not needed (always cookie-less browse path)
#   9  1-Write questions to txt (py)          → questions.txt via export agent
#   10 1-Save raw data S3 (py)                → SKIPPED: no upstream S3 — everything stays on local fs
#   11 Submit Query thru API (py)             → SKIPPED (upstream vendor backend)
#   12 2-Parse answers (py, random sample)    → pure code, DETERMINISTIC sample (no RNG allowed here)
#   13 2-Find all competitors (gpt-4.1)       → agent (faithful prompt)
#   14 2-Reformat competitor list (gpt-4.1)   → agent (faithful prompt, schema-forced list)
#   15 2-Pull competitors from API (py)       → SKIPPED (upstream vendor backend); LLM list is sole source
#   16 2-Calculate Visibility Score (py)      → pure port (same math, same output keys)
#   17 2-Submit score thru API (py)           → SKIPPED (upstream vendor backend); saved locally instead
#   18 3-Parse reference to csv (py)          → pure port (zero-count rows omitted — step 22
#                                               filtered them out anyway)
#   19 3-Parse domain relations (py)          → pure port (url normalization, reuse stats)
#   20 3-Analyze domain categories (ai)       → agent (faithful prompt, schema-forced)
#   21 3-Domain categories 3D CSV (py)        → pure code builds domain_summary.csv (export agent writes)
#   22 3-Combine ref data (py)                → pure port (full analysis JSON shape)
#   23 3-Domain reference list (py→HTML+S3)   → pure code HTML (condensed), saved locally, no S3/API
#   24 3-Write ref domain report (ai)         → agent (faithful prompt; hardcoded "blood pressure
#                                               monitors" example genericized to args topic)
#   25 3-Save report to file (py)             → export agent
#   26 Upload to S3 (py)                      → SKIPPED: local fs only (user decision)
#   27 Collect results (py)                   → workflow return value
#
# Cookie-less + incremental design (user decision): the browser runs logged
# out (fresh profile, no cookies), and ChatGPT / Google / Perplexity tolerate
# only a few anonymous queries at a time. So each run scrapes at most
# `max_questions` not-yet-covered questions (default 3), records per-platform
# success/blocked status, and recomputes all analysis over the FULL
# accumulated corpus. Schedule the workflow daily to grow coverage gradually;
# failed/blocked (platform, question) pairs are retried on later runs.
#
# Browser prerequisites (empirical, 2026-06-09):
#   - The browse daemon must be STARTED FROM THE MAIN SESSION — Claude Code's
#     command sandbox blocks workflow subagents from cold-starting it (hangs
#     on "[browse] Starting server...").
#   - It must run in HEADED stealth mode (/connect-chrome): headless mode is
#     bot-blocked by all three platforms on the very first anonymous query;
#     headed stealth passes all three without any login.
#
# Input : args = { brand (required), brandLink?, topic?, questions?: list[str]|str,
#                  questionsCsv?: path, maxQuestions?: 3, platforms?: subset of
#                  ['chatgpt','google-ai','perplexity'], outputDir? }
#   - questions source precedence: args.questions > args.questionsCsv >
#     derived ./demos/aeo-output/user-prompt-research/<topic-slug>-questions.csv
# Output: { jobId, visibility, topCompetitors, topDomains, reportPath, ... }
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
    raise RuntimeError(
        'No brand provided. Pass args like { brand: "Cursor", topic: "ai coding assistant" } '
        'or { brand: "Cursor", questions: ["...", "..."] }.'
    )
brand_link = str(input.get("brandLink") or "").strip()
topic = str(input.get("topic") or "").strip()


# Canonical slugify — keep IDENTICAL across all marketing workflows: the
# CSV/workspace handoff between pipeline stages depends on matching slugs.
def slugify(s, max=80):
    out = re.sub(r"[^a-z0-9]+", "-", s.lower())
    out = re.sub(r"^-+|-+$", "", out)[:max]
    out = re.sub(r"-+$", "", out)
    return out or "item"


brand_slug = slugify(brand, 40)
workspace = re.sub(r"/+$", "", input.get("outputDir") or f"./demos/aeo-output/aeo-data-scientist/{brand_slug}")
max_questions = math.floor(float(input.get("maxQuestions"))) if (input.get("maxQuestions") is not None and float(input.get("maxQuestions")) > 0) else 3
ALL_PLATFORMS = ["chatgpt", "google-ai", "perplexity"]
platforms = (
    [p for p in input["platforms"] if p in ALL_PLATFORMS]
    if isinstance(input.get("platforms"), list) and len(input["platforms"])
    else ALL_PLATFORMS
)
if not platforms:
    raise RuntimeError(f"platforms must be a subset of {', '.join(ALL_PLATFORMS)}")

# explicit questions (array or newline string), else CSV path
explicit_questions = []
if isinstance(input.get("questions"), list):
    explicit_questions = input["questions"]
elif isinstance(input.get("questions"), str):
    explicit_questions = input["questions"].split("\n")
explicit_questions = [q2 for q2 in (str(q).strip() for q in explicit_questions) if len(q2) > 0]

csv_path = str(input.get("questionsCsv") or "").strip()
if not explicit_questions and not csv_path and topic:
    csv_path = f"./demos/aeo-output/user-prompt-research/{slugify(topic)}-questions.csv"
if not explicit_questions and not csv_path:
    raise RuntimeError(
        'No questions provided. Pass questions: [...], questionsCsv: "<path>", or topic: "<keyword>" '
        "(topic derives the CSV written by /user-prompt-research). Run /user-prompt-research first if needed."
    )
topic_label = topic or f"{brand}-related products/services"

log(f"Brand: \"{brand}\"  ·  workspace: {workspace}  ·  batch: up to {max_questions} question(s) × [{', '.join(platforms)}]  ·  no-login browser mode")

# ── JSON Schemas for schema-forced agent steps ───────────────────────────────
INIT_SCHEMA = {
    "type": "object",
    "properties": {
        "runTime": {"type": "string"},        # "YYYY-MM-DD HH:MM:SS"
        "runStamp": {"type": "string"},       # "YYYY_MM_DD_HH_MM_SS"
        "daemonHealthy": {"type": "boolean"},  # gstack browse daemon reachable?
        "daemonMode": {"type": "string"},     # 'headed' | 'launched' (headless) | 'none'
        "csvQuestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "volume": {"type": "string"}},
                "required": ["question", "volume"],
                "additionalProperties": False,
            },
        },
        "covered": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "file": {"type": "string"},
                    "question": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["platform", "file", "question", "status"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["runTime", "runStamp", "daemonHealthy", "daemonMode", "csvQuestions", "covered"],
    "additionalProperties": False,
}

# Sandboxed subagents CANNOT cold-start the browse daemon (the sandbox blocks
# the server bind and the CLI hangs on "[browse] Starting server..."). They can
# only talk to an ALREADY-RUNNING daemon. Every browse call therefore goes
# through this hang-proof wrapper, and Init verifies the daemon up-front.
BROWSE_WRAPPER = """Run every browse command through this hang-proof wrapper (NEVER call the browse binary directly — a cold daemon start hangs forever in this sandbox):
python3 -c "import subprocess,os,sys; r=subprocess.run([os.path.expanduser('~/.claude/skills/gstack/browse/dist/browse')]+sys.argv[1:],capture_output=True,text=True,timeout=90); print(r.stdout); print(r.stderr,file=sys.stderr)" <command> <args...>
If a call raises TimeoutExpired twice in a row, stop using the browser and mark the affected platform(s) failed with note "browse timeout". Never let any single Bash call run longer than ~120 seconds."""

SCRAPE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string", "enum": ALL_PLATFORMS},
                    "status": {"type": "string", "enum": ["completed", "failed"]},
                    "answerChars": {"type": "integer"},
                    "sourcesCount": {"type": "integer"},
                    "note": {"type": "string"},     # '' when fine; 'blocked: login wall', 'captcha', etc.
                },
                "required": ["platform", "status", "answerChars", "sourcesCount", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

CORPUS_SCHEMA = {
    "type": "object",
    "properties": {
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
    "required": ["answers"],
    "additionalProperties": False,
}

COMPETITORS_SCHEMA = {
    "type": "object",
    "properties": {"competitors": {"type": "array", "items": {"type": "string"}}},
    "required": ["competitors"],
    "additionalProperties": False,
}

DOMCAT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "total_citations": {"type": "integer"},
                    "category": {"type": "string"},
                    "category_name": {"type": "string"},
                },
                "required": ["domain", "total_citations", "category", "category_name"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

EXPORT_SCHEMA = {
    "type": "object",
    "properties": {"files": {"type": "array", "items": {"type": "string"}}},
    "required": ["files"],
    "additionalProperties": False,
}


# ── pure-code ports of the upstream python transforms ─────────────────────────
def escape_regex(s):
    return re.sub(r"[.*+?^${}()|[\]\\]", lambda m: "\\" + m.group(0), s)


# step-16 get_domain_from_url / step-18 extract_domain_from_url
def domain_from_url(url):
    if not url:
        return ""
    u = url.strip()
    if not re.match(r"^https?://", u, re.IGNORECASE):
        u = "https://" + u
    m = re.match(r"^https?://([^/?#]+)", u, re.IGNORECASE)
    domain = (m.group(1) if m else "").lower()
    domain = re.sub(r"^www\.", "", domain)
    domain = re.sub(r":\d+$", "", domain)
    return domain


# step-19 normalize_url
def normalize_url(url):
    base = url.split("?")[0].split("#")[0]
    if base.endswith("/"):
        base = base[:-1]
    return base


def csv_field(v):
    s = str("" if v is None else v)
    return '"' + s.replace('"', '""') + '"' if re.search(r"[\",\n]", s) else s


# "10~50" → 30 midpoint for sorting; plain ints pass through
def volume_to_number(v):
    s = str("" if v is None else v).strip()
    rng = re.match(r"^(\d+)\s*~\s*(\d+)$", s)
    if rng:
        return (int(rng.group(1)) + int(rng.group(2))) / 2
    try:
        n = float(s)
    except (ValueError, TypeError):
        return 0
    return n if math.isfinite(n) else 0


# step-12 port: per-platform sample of completed answers, joined as one text.
# Original used random.sample(n/3, max 10); RNG is banned in workflow scripts,
# so we take an evenly-spaced deterministic sample of the same size.
def sample_answer_text(corpus):
    texts = []
    for platform in ALL_PLATFORMS:
        entries = sorted(
            (a for a in corpus if a["platform"] == platform and a["status"] == "completed" and a["answer"].strip()),
            key=lambda x: x["question"],
        )
        if not entries:
            continue
        n = len(entries)
        size = min(max(1, math.floor(n / 3)), 10, n)
        for k in range(size):
            idx = math.floor((k * n) / size)
            texts.append(re.sub(r"[\r\n]+", " ", entries[idx]["answer"]))
    return "\n".join(texts)


# step-16 extract_ai_content equivalent over our canonical answer entries
def answer_content(entry):
    source_text = " ".join(f"{domain_from_url(s['url'])} {s.get('title') or ''}" for s in entry["sources"])
    return f"{entry['answer']} {source_text}"


def mention_count(name, content):
    if not name or not content:
        return 0
    rx = re.compile(rf"\b{escape_regex(name)}\b", re.IGNORECASE)
    return len(rx.findall(content))


# step-16 analyze_brand_visibility port (same output keys)
def analyze_brand_visibility(corpus, brand_name, link):
    valid = [a for a in corpus if a["status"] == "completed" and a["answer"].strip()]
    if not valid:
        return {
            "brand_name": brand_name, "brand_link": link, "brand_logo": "",
            "brand_visibility_score_percentage": 0.0,
            "brand_link_visibility_score_percentage": 0.0,
            "brand_mentions": 0, "brand_link_mentions": 0,
            "platform_visibility_score_percentage_list": [],
        }
    link_domain = domain_from_url(link)
    total_score = 0
    total_link_score = 0
    total_mentions = 0
    total_link_mentions = 0
    platform_stats = {}
    for entry in valid:
        content = answer_content(entry)
        mentions = mention_count(brand_name, content)
        link_mentions = mention_count(link_domain, content) if link_domain else 0
        total_score += 1 if mentions > 0 else 0
        total_link_score += 1 if link_mentions > 0 else 0
        total_mentions += mentions
        total_link_mentions += link_mentions
        p = entry["platform"]
        if p not in platform_stats:
            platform_stats[p] = {"total": 0, "withBrand": 0, "mentions": 0, "withLink": 0, "linkMentions": 0}
        platform_stats[p]["total"] += 1
        if mentions > 0:
            platform_stats[p]["withBrand"] += 1
        platform_stats[p]["mentions"] += mentions
        if link_mentions > 0:
            platform_stats[p]["withLink"] += 1
        platform_stats[p]["linkMentions"] += link_mentions

    def round2(x):
        # Faithful to JS Math.round (round half AWAY from zero); Python's round()
        # is banker's rounding and would differ on exact .5 ties (e.g. 3.125).
        # All inputs here are non-negative percentages, so floor(x + 0.5) suffices.
        return math.floor(x * 100 + 0.5) / 100

    platform_list = [
        {
            "platform_name": name,
            "platform_logo": "",
            "platform_visibility_score_percentage": round2((s["withBrand"] / s["total"]) * 100),
            "platform_brand_link_visibility_score_percentage": round2((s["withLink"] / s["total"]) * 100),
            "platform_brand_mentions": s["mentions"],
            "platform_brand_link_mentions": s["linkMentions"],
        }
        for name, s in platform_stats.items()
    ]
    return {
        "brand_name": brand_name, "brand_link": link, "brand_logo": "",
        "brand_visibility_score_percentage": round2((total_score / len(valid)) * 100),
        "brand_link_visibility_score_percentage": round2((total_link_score / len(valid)) * 100),
        "brand_mentions": total_mentions, "brand_link_mentions": total_link_mentions,
        "platform_visibility_score_percentage_list": platform_list,
    }


# step-18 port: platform×question×domain citation matrix + domain summary.
# Deviation: long-format rows with citation_count === 0 are omitted (step-22
# filtered count>0 anyway, and zero rows explode the CSV as coverage grows).
def build_citation_matrix(corpus):
    matrix = {}
    all_domains = set()
    for entry in corpus:
        if entry["status"] != "completed":
            continue
        q = entry["question"]
        if len(q) > 100:
            q = q[:97] + "..."
        if not q:
            continue
        domains = [d for d in (domain_from_url(s["url"]) for s in entry["sources"]) if d]
        if not domains:
            continue
        p = entry["platform"]
        matrix[p] = matrix.get(p) or {}
        matrix[p][q] = matrix[p].get(q) or {}
        for d in domains:
            matrix[p][q][d] = matrix[p][q].get(d, 0) + 1
            all_domains.add(d)
    rows = []
    for p in sorted(matrix.keys()):
        for q in sorted(matrix[p].keys()):
            for d in sorted(matrix[p][q].keys()):
                rows.append({"platform": p, "question": q, "domain": d, "citation_count": matrix[p][q][d]})
    totals = {}
    for r in rows:
        totals[r["domain"]] = totals.get(r["domain"], 0) + r["citation_count"]
    domain_summary = sorted(
        ({"domain": d, "total_citations": totals.get(d, 0), "category": ""} for d in sorted(all_domains)),
        key=lambda a: a["total_citations"],
        reverse=True,
    )
    return {"rows": rows, "domainSummary": domain_summary, "matrix": matrix}


# step-19 port: domain → url/platform/question mapping with reuse stats
def build_url_mapping(corpus):
    mapping = {}
    for entry in corpus:
        if entry["status"] != "completed":
            continue
        q = entry["question"]
        if len(q) > 100:
            q = q[:97] + "..."
        if not q:
            continue
        for s in entry["sources"]:
            url = (s.get("url") or "").strip()
            domain = domain_from_url(url)
            if not domain:
                continue
            norm = normalize_url(url)
            m = mapping.get(domain) or {"citations": {}, "urlUsage": {}, "platforms": set(), "urls": set()}
            mapping[domain] = m
            m["urlUsage"][norm] = m["urlUsage"].get(norm) or []
            if q not in m["urlUsage"][norm]:
                m["urlUsage"][norm].append(q)
            key = f"{norm}\x00{q}"
            if m["citations"].get(key):
                if entry["platform"] not in m["citations"][key]["platforms"]:
                    m["citations"][key]["platforms"].append(entry["platform"])
            else:
                m["citations"][key] = {"url": url, "platform": entry["platform"], "platforms": [entry["platform"]], "question": q}
            m["platforms"].add(entry["platform"])
            m["urls"].add(norm)
    final = {}
    for domain in sorted(mapping.keys()):
        m = mapping[domain]
        citations = [
            {
                "url": c["url"],
                "platform": ", ".join(sorted(c["platforms"])) if len(c["platforms"]) > 1 else c["platform"],
                "question": c["question"],
            }
            for c in m["citations"].values()
        ]
        reuse = [qs for qs in m["urlUsage"].values() if len(qs) > 1]
        final[domain] = {
            "url_citations": citations,
            "total_platform_question_unique_citations": len(citations),
            "unique_urls": len(m["urls"]),
            "platforms": sorted(m["platforms"]),
            "url_reuse": len(reuse),
            "max_url_reuse": max((len(qs) for qs in reuse)) if reuse else 0,
        }
    return final


# step-22 port: combine matrix + category map into the report-input JSON
def combine_ref_data(rows, category_map):
    overall = {}
    platform_domains = {}
    question_domains = {}
    pq_domains = {}
    totals = {}
    platforms2 = set()
    questions = set()
    domains = set()
    for r in rows:
        if r["citation_count"] <= 0:
            continue
        platforms2.add(r["platform"])
        questions.add(r["question"])
        domains.add(r["domain"])
        totals[r["domain"]] = totals.get(r["domain"], 0) + r["citation_count"]
        overall[r["domain"]] = overall.get(r["domain"], 0) + 1
        platform_domains[r["platform"]] = platform_domains.get(r["platform"]) or {}
        platform_domains[r["platform"]][r["domain"]] = platform_domains[r["platform"]].get(r["domain"], 0) + 1
        question_domains[r["question"]] = question_domains.get(r["question"]) or {}
        question_domains[r["question"]][r["domain"]] = question_domains[r["question"]].get(r["domain"], 0) + 1
        pq = pq_domains.get(r["platform"]) or {}
        pq_domains[r["platform"]] = pq
        pq[r["question"]] = pq.get(r["question"]) or {}
        pq[r["question"]][r["domain"]] = pq[r["question"]].get(r["domain"], 0) + 1

    def cat(d):
        return category_map.get(d) or "unknown"

    result = {
        "summary": {
            "total_platforms": len(platforms2),
            "total_questions": len(questions),
            "total_unique_domains": len(domains),
            "total_platform_question_pairs": sum(overall.values()),
            "total_actual_citations": sum(totals.values()),
        },
        "overall": {
            "description": "Domain occurrence frequency across platform-question combinations",
            "all_domains": sorted(
                (
                    {"domain": d, "category": cat(d), "platform_question_occurrences": c, "actual_citation_total": totals[d]}
                    for d, c in overall.items()
                ),
                key=lambda a: a["platform_question_occurrences"],
                reverse=True,
            ),
        },
        "by_platform": {},
        "by_question": {},
        "platform_question_breakdown": {},
    }
    for p in sorted(platforms2):
        pd = platform_domains[p]
        result["by_platform"][p] = {
            "total_question_occurrences": sum(pd.values()),
            "unique_domains": len(pd.keys()),
            "all_domains": sorted(
                ({"domain": d, "category": cat(d), "question_occurrences": c} for d, c in pd.items()),
                key=lambda a: a["question_occurrences"],
                reverse=True,
            ),
        }
    for q in sorted(questions):
        qd = question_domains[q]
        display = q[:100] + "..." if len(q) > 100 else q
        result["by_question"][display] = {
            "full_question": q,
            "total_platform_occurrences": sum(qd.values()),
            "unique_domains": len(qd.keys()),
            "all_domains": sorted(
                ({"domain": d, "category": cat(d), "platform_occurrences": c} for d, c in qd.items()),
                key=lambda a: a["platform_occurrences"],
                reverse=True,
            ),
        }
    for p in sorted(platforms2):
        list_ = []
        for q, dd in (pq_domains.get(p) or {}).items():
            display = q[:80] + "..." if len(q) > 80 else q
            list_.append({
                "question": display,
                "total_domain_occurrences": sum(dd.values()),
                "all_domains": sorted(
                    ({"domain": d, "category": cat(d), "occurrences": c} for d, c in dd.items()),
                    key=lambda a: a["occurrences"],
                    reverse=True,
                ),
            })
        if list_:
            result["platform_question_breakdown"][p] = list_
    # comparative analysis
    domain_platforms = {}
    for p, pd in platform_domains.items():
        for d in pd.keys():
            domain_platforms[d] = domain_platforms.get(d) or set()
            domain_platforms[d].add(p)
    universal = sorted(
        (
            {
                "domain": d, "category": cat(d), "platforms": sorted(ps),
                "total_platform_question_occurrences": overall[d], "actual_citation_total": totals[d],
            }
            for d, ps in domain_platforms.items()
            if len(ps) == len(platforms2)
        ),
        key=lambda a: a["total_platform_question_occurrences"],
        reverse=True,
    )
    specific = {}
    for d, ps in domain_platforms.items():
        if len(ps) == 1:
            p = next(iter(ps))
            specific[p] = specific.get(p) or []
            specific[p].append({"domain": d, "category": cat(d), "question_occurrences": platform_domains[p][d]})
    for p in specific.keys():
        specific[p].sort(key=lambda a: a["question_occurrences"], reverse=True)
    result["comparative_analysis"] = {
        "universal_domains": universal,
        "platform_specific_domains": specific,
        "domain_coverage": {
            "domains_in_all_platforms": len(universal),
            "domains_in_single_platform": sum(len(l) for l in specific.values()),
        },
    }
    return result


# step-23 port (condensed): static HTML reference list, grouped by category
def build_reference_html(url_mapping, cat_name_map, brand_name, run_time):
    by_category = {}
    for domain, info in url_mapping.items():
        cat_name = cat_name_map.get(domain) or "Other"
        by_category[cat_name] = by_category.get(cat_name) or []
        by_category[cat_name].append({"domain": domain, "info": info})

    def esc(s):
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Domain Reference List</title>',
        "<style>body{font-family:Georgia,serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222}",
        "h2{border-bottom:2px solid #b5651d;padding-bottom:4px}h3{margin-bottom:2px}",
        ".meta{color:#666;font-size:0.9em}li{margin:2px 0}</style></head><body>",
        f'<h1>Domain Reference List</h1><p class="meta">Brand: {esc(brand_name)} · Generated: {esc(run_time)} · AI-engine citations grouped by domain category</p>',
    ]
    for cat_name in sorted(by_category.keys()):
        parts.append(f"<h2>{esc(cat_name)}</h2>")
        for item in sorted(by_category[cat_name], key=lambda a: a["info"]["total_platform_question_unique_citations"], reverse=True):
            domain = item["domain"]
            info = item["info"]
            parts.append(f"<h3>{esc(domain)}</h3>")
            parts.append(f'<p class="meta">{info["total_platform_question_unique_citations"]} citation(s) · {info["unique_urls"]} unique URL(s) · platforms: {esc(", ".join(info["platforms"]))} · url_reuse: {info["url_reuse"]} (max {info["max_url_reuse"]})</p>')
            parts.append("<ul>")
            for c in info["url_citations"]:
                # citations come from scraped web data — only hyperlink http(s) schemes
                if re.match(r"^https?://", c["url"], re.IGNORECASE):
                    parts.append(f'<li><a href="{esc(c["url"])}">{esc(normalize_url(c["url"]))}</a> — <em>{esc(c["platform"])}</em> — {esc(c["question"])}</li>')
                else:
                    parts.append(f'<li>{esc(c["url"])} — <em>{esc(c["platform"])}</em> — {esc(c["question"])}</li>')
            parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ═══ Phase: Init — timestamps, dirs, question CSV, coverage scan ═════════════
phase("Init")

init_prompt = f"""You are initializing a local AEO research workspace. Do these steps exactly; everything is on the local filesystem relative to the current working directory.

1. Run `date '+%Y-%m-%d %H:%M:%S'` → return as runTime. Run `date '+%Y_%m_%d_%H_%M_%S'` → return as runStamp.
2. Ensure these directories exist (mkdir -p): {"  ".join(f"{workspace}/answers/{p}" for p in platforms)}
3. {
    f'Read the CSV file at "{csv_path}" (columns: Time,Question,Search Volume). Return every row as {{question, volume}} (volume as the raw string, e.g. "140" or "10~50"). If the file does not exist, return an empty csvQuestions array.'
    if csv_path
    else "No CSV to read — return an empty csvQuestions array."
}
4. Scan {workspace}/answers/*/*.json — for EACH json file found, parse it and return {{platform: <parent dir name>, file: <basename>, question: <result.question or "">, status: <its "status" field or "failed">}}. If the directory is empty or missing, return an empty covered array.
5. Check the gstack browse daemon WITHOUT risking a hang — run exactly:
python3 -c "import subprocess,os; r=subprocess.run([os.path.expanduser('~/.claude/skills/gstack/browse/dist/browse'),'status'],capture_output=True,text=True,timeout=20); print(r.stdout)"
→ daemonHealthy = true only if it prints "Status: healthy" within the timeout. On TimeoutExpired, any exception, or other output: daemonHealthy = false (do NOT retry, do NOT try to start the daemon).
→ daemonMode = "headed" if the output contains "Mode: headed", "launched" if it contains "Mode: launched", otherwise "none".

Use bash (ls/date/mkdir) and python3 for parsing. Return ONLY the structured object."""

init = await agent(init_prompt, label="init-workspace", phase="Init", schema=INIT_SCHEMA)
if not init:
    raise RuntimeError("Init agent failed — cannot determine workspace state.")
run_time = init["runTime"].strip()
run_stamp = init["runStamp"].strip()
job_id = f"AEO_{brand_slug}_{run_stamp}"

# Build the master question list (explicit > CSV sorted by volume desc)
if explicit_questions:
    all_questions = explicit_questions
else:
    all_questions = [
        r["q"]
        for r in sorted(
            (
                {"q": r["question"].strip(), "v": volume_to_number(r["volume"])}
                for r in init["csvQuestions"]
            ),
            key=lambda a: a["v"],
            reverse=True,
        )
        if len(r["q"]) > 0 and "?" in r["q"]
    ]
    # CSV merge keeps duplicates across runs — dedupe, keeping highest-volume order
    all_questions = list(dict.fromkeys(all_questions))
if not all_questions:
    raise RuntimeError(f'No usable questions found{f" in {csv_path}" if csv_path else ""}. Run /user-prompt-research "{topic or brand}" first, or pass questions explicitly.')

# Coverage: (platform → set of covered question slugs with status completed)
covered_set = set(
    f"{c['platform']}\x00{slugify(c['question'])}"
    for c in init["covered"]
    if c["status"] == "completed"
)
pending = []   # [{question, slug, missingPlatforms}]
for q in all_questions:
    slug = slugify(q)
    missing = [p for p in platforms if f"{p}\x00{slug}" not in covered_set]
    if missing:
        pending.append({"question": q, "slug": slug, "missingPlatforms": missing})
batch = pending[:max_questions]
if not init["daemonHealthy"] and batch:
    log("⚠ gstack browse daemon is NOT running — skipping scraping this run (sandboxed agents cannot start it). Start it from the main session (e.g. run `browse status` via the /browse skill), then re-run.")
    batch = []
elif batch and init["daemonMode"] != "headed" and not input.get("allowHeadless"):
    # Empirical (2026-06-09): in HEADLESS mode all three platforms hard-block the
    # anonymous browser on the first query (ChatGPT/Perplexity: Cloudflare loop,
    # Google: sorry-page captcha). Headed stealth mode (/connect-chrome) passes.
    log("⚠ browse daemon is running HEADLESS — all 3 platforms bot-block anonymous headless browsers, so scraping is skipped. Run /connect-chrome in the main session for headed stealth mode (or pass allowHeadless: true to force).")
    batch = []
log(f"Questions: {len(all_questions)} total · {len(pending)} with missing platform coverage · scraping {len(batch)} this run (job {job_id})")

# ═══ Phase: Scrape — sequential, one agent per question, no cookies ══════════
phase("Scrape")

scrape_status = []  # [{question, results: [{platform, status, note}]}]
for item in batch:
    scrape_prompt = f"""You are collecting AI-answer-engine responses for AEO (Answer Engine Optimization) research. You operate a headless browser WITHOUT any login cookies (sandbox environment). Work gently: one question, {len(item["missingPlatforms"])} platform(s), at most ONE retry per platform. If a platform shows a login wall, captcha, or bot block, mark it failed — do NOT fight it.

QUESTION: {item["question"]}

PLATFORMS TO QUERY (only these): {", ".join(item["missingPlatforms"])}

Browser: the gstack browse daemon is ALREADY RUNNING (verified). {BROWSE_WRAPPER}
Useful commands: goto <url> · text · snapshot -i · click <sel|@ref> · fill <sel> <val> · type <text> · press Enter · wait --networkidle · links · reload. Take a fresh "text" or "snapshot -i" after each wait to check streaming progress; poll with short waits instead of one long one.

Bot-block handling: if a page shows a Cloudflare/"verify you are human" challenge or a hard login wall, wait ~10s, reload ONCE; if still blocked, mark that platform failed with note "blocked: <reason>" and move on.

Per-platform instructions:
{"- chatgpt: goto https://chatgpt.com — dismiss any consent/" + chr(34) + "stay logged out" + chr(34) + " dialogs. Fill the prompt box with the question, submit, then poll (wait a few seconds + re-read text) until the response stops growing. Extract the assistant's full answer as plain text, plus any cited/linked source URLs in the answer." + chr(10) if "chatgpt" in item["missingPlatforms"] else ""}{"- google-ai: goto https://www.google.com/search?udm=50&q=<url-encoded question> (Google AI Mode). If AI Mode is unavailable without login, fall back to a regular search https://www.google.com/search?q=... and use the " + chr(34) + "AI Overview" + chr(34) + " block (click " + chr(34) + "Show more" + chr(34) + " if present). Extract the AI-generated answer text and the cited source links (url + title). If there is NO AI-generated answer at all, mark failed with note " + chr(34) + "no AI answer" + chr(34) + "." + chr(10) if "google-ai" in item["missingPlatforms"] else ""}{"- perplexity: goto https://www.perplexity.ai/search?q=<url-encoded question> (or the homepage search box). Poll until the answer finishes. Extract the answer text and the numbered source citations (url + title)." + chr(10) if "perplexity" in item["missingPlatforms"] else ""}
After scraping, write ONE json file per platform (including failed ones) using a python3 script. Path: {workspace}/answers/<platform>/{item["slug"]}.json (overwrite if present). Get TS_MS via python3 int(time.time()*1000). Exact JSON shape:
{{
  "scriptName": "search-<platform>",
  "status": "completed" | "failed",
  "timestamp": TS_MS,
  "result": {{
    "question": <the question>,
    "answer": <full plain-text answer, or "" if failed>,
    "answerLength": <len of answer>,
    "sources": [{{"url": "...", "title": "..."}}],
    "sourcesCount": <len of sources>
  }},
  "error": null | "<short reason>"
}}
Rules: status "completed" requires a non-empty real answer actually scraped from the platform. NEVER fabricate an answer or sources from your own knowledge — if scraping failed, status is "failed". Keep answer text as-is (do not summarize it).

Return ONLY the structured object: results = one entry per platform with {{platform, status, answerChars, sourcesCount, note}} (note "" when fine)."""

    out = await agent(scrape_prompt, label=f"ask:{item['slug'][:40]}", phase="Scrape", schema=SCRAPE_SCHEMA)
    results = (out and out.get("results")) or [
        {"platform": p, "status": "failed", "answerChars": 0, "sourcesCount": 0, "note": "scrape agent failed"}
        for p in item["missingPlatforms"]
    ]
    scrape_status.append({"question": item["question"], "results": results})
    line_summary = " ".join(f"{r['platform']}:{'ok' if r['status'] == 'completed' else 'FAIL'}" for r in results)
    log(f'"{item["question"][:60]}..." → {line_summary}')

# per-platform health for this run
platform_health = {}
for p in platforms:
    rs = [r for s in scrape_status for r in s["results"] if r["platform"] == p]
    platform_health[p] = {
        "attempted": len(rs),
        "completed": len([r for r in rs if r["status"] == "completed"]),
        "notes": list(dict.fromkeys(r["note"] for r in rs if r["note"])),
    }

# ═══ Load full accumulated corpus (previous runs + this run) ═════════════════
corpus_prompt = f"""Read ALL json files under {workspace}/answers/*/*.json on the local filesystem (use python3; the answers/<platform>/ dir name is the platform). For each file return one entry:
{{platform, question: result.question, status: <"status" field>, answer: <result.answer, or result.aiGenerated.formattedContent if answer is missing/empty; truncate to 6000 chars>, sources: [{{url, title}}] from result.sources (use "" for a missing title; for source items that are plain strings, treat the string as the url)}}.
Skip unparseable files. Return ONLY the structured object. If there are no files, return {{"answers": []}}."""

corpus_out = await agent(corpus_prompt, label="load-corpus", phase="Scrape", schema=CORPUS_SCHEMA)
corpus = [a for a in ((corpus_out and corpus_out.get("answers")) or []) if a["question"] and a["question"].strip()]
valid_corpus = [a for a in corpus if a["status"] == "completed" and a["answer"].strip()]
log(f"Corpus: {len(corpus)} answer files, {len(valid_corpus)} valid (completed) answers")
if not valid_corpus:
    raise RuntimeError(
        f"No successful answers in the corpus (platform status: {json.dumps(platform_health)}; browse daemon healthy: {init['daemonHealthy']}). "
        "Nothing to analyze. If the daemon was down, start it from the main session (any /browse command) and re-run. "
        "If platforms blocked the anonymous browser, try again later, reduce maxQuestions, or import cookies via /setup-browser-cookies."
    )

# ═══ Phase: Competitors — steps 12/13/14 (15 skipped: upstream API) ═══════════
phase("Competitors")

sampled_answers = sample_answer_text(corpus)
questions_joined = "\n".join(all_questions)

# step-13 prompt, faithful
find_competitors_prompt = f"""Task: Extract competitor brand names from the AI response.

Variables:
QUESTIONS = {questions_joined}
AI_ANSWER = {sampled_answers}

Context: Based on QUESTIONS, determine the product/service category and usage context being discussed.

Definition - A competitor is a brand/product line that:
- Offers products/services in the same primary category as discussed in QUESTIONS
- Targets similar usage context and customer segment
- Is explicitly mentioned in the AI response

Inclusion rules (ALL required):
- Name appears verbatim in the AI response
- Represents a brand or product line name (no model numbers)
- Relevant to the category implied by QUESTIONS
- When uncertain, omit

Exclusion rules:
- Generic terms, technologies, categories (non-proper nouns)
- Parent companies without specific product lines mentioned
- Marketplaces/platforms unless they ARE the product category
- Accessories or complementary items (unless that's your category)
- Products/services outside the usage context of QUESTIONS

Output format:
Return only valid competitor names, comma-separated, no extra formatting.
If none found, return empty string."""

raw_competitors = (await agent(find_competitors_prompt, label="find-competitors", phase="Competitors")) or ""

# step-14 prompt, faithful (output schema-forced instead of one-per-line text)
reformat_prompt = f"""Inputs
RAW_LIST = {raw_competitors}
MY_BRAND = {brand}

IMPORTANT:
RAW_LIST is the ONLY allowed source of brand names.
Do NOT generate, infer, explain, or fabricate any names.
If RAW_LIST is empty or contains no valid brands return an empty list.

Goal
Clean and deduplicate RAW_LIST, exclude anything that equals MY_BRAND, exclude any empty strings.
Rules (apply in this order):
Parse & trim: Split on commas and line breaks; trim whitespace. Remove surrounding quotes/brackets/hashtags and trailing punctuation.
Canonicalization for matching (not for inventing new names):
Case-insensitive comparison.
Ignore common legal suffixes for equality checks (e.g., Inc., LLC, Ltd., Co., GmbH, S.A., S.r.l., Pvt. Ltd., Co., Ltd., PLC, LLP, BV, NV, AB, AG, KK).
Treat minor punctuation/spacing variants as the same (hyphens, extra spaces, periods).
Exclude self: Remove any entry that equals MY_BRAND under the canonicalization above, or is an obvious alias/house style of MY_BRAND present in RAW_LIST.
Scope guard: Keep only brand or product-line names (proper nouns). Discard generic descriptors, categories, feature terms, model numbers/SKUs, and non-brand entities.
Deduplicate: Preserve the first appearance order from RAW_LIST.
Use the original surface form from RAW_LIST (except trimming/quote removal).

Return ONLY the structured object: {{competitors: [...]}}."""

comp_out = await agent(reformat_prompt, label="clean-competitors", phase="Competitors", schema=COMPETITORS_SCHEMA)
competitors = list(dict.fromkeys(
    c2 for c2 in (c.strip() for c in ((comp_out and comp_out.get("competitors")) or []))
    if c2 and c2.lower() != brand.lower()
))
log(f"Competitors found in AI answers: {', '.join(competitors) if competitors else '(none)'}")

# ═══ Phase: Visibility — step 16 pure-code port (step 17 skipped) ════════════
phase("Visibility")

visibility = analyze_brand_visibility(corpus, brand, brand_link)
visibility["competitors"] = [
    {
        "brand_name": c,
        "brand_logo": "",
        "brand_visibility_score_percentage": r["brand_visibility_score_percentage"],
        "platform_visibility_score_percentage_list": r["platform_visibility_score_percentage_list"],
    }
    for c, r in ((c, analyze_brand_visibility(corpus, c, "")) for c in competitors)
]
log(f'Brand visibility: {visibility["brand_visibility_score_percentage"]}% of {len(valid_corpus)} valid answers mention "{brand}"')


# visibility_history.csv row (trend across scheduled runs)
def plat_pct(p):
    e = next((x for x in visibility["platform_visibility_score_percentage_list"] if x["platform_name"] == p), None)
    return e["platform_visibility_score_percentage"] if e else ""


history_header = "Time,Job,Valid Answers,Brand %,Link %,chatgpt %,google-ai %,perplexity %"
history_row = ",".join(csv_field(v) for v in [run_time, job_id, len(valid_corpus), visibility["brand_visibility_score_percentage"], visibility["brand_link_visibility_score_percentage"], plat_pct("chatgpt"), plat_pct("google-ai"), plat_pct("perplexity")])

export_visibility_prompt = f"""Write these local files using a python3 script (create parent dirs; overwrite unless told to append). Return the structured object {{files: [<paths written>]}}.

1. WRITE {workspace}/visibility_score.json with EXACTLY this JSON content:
{json.dumps(visibility, indent=2)}

2. APPEND to {workspace}/visibility_history.csv: if the file does not exist, first write the header line "{history_header}". Then append this row:
{history_row}

3. WRITE {workspace}/questions.txt — one question per line:
{chr(10).join(all_questions)}"""

vis_export = await agent(export_visibility_prompt, label="export-visibility", phase="Visibility", schema=EXPORT_SCHEMA)

# ═══ Phase: Domains — steps 18-23 (pure code + one LLM categorization) ════════
phase("Domains")

_matrix = build_citation_matrix(corpus)
matrix_rows = _matrix["rows"]
domain_summary = _matrix["domainSummary"]
url_mapping = build_url_mapping(corpus)
log(f"Citations: {len(matrix_rows)} (platform,question,domain) rows across {len(domain_summary)} unique domains")

# step-20 prompt, faithful (input = step-18 domain_summary), schema-forced
category_map = {}
cat_name_map = {}
domcat_items = []
if domain_summary:
    domcat_prompt = f"""From the json input:
{json.dumps({"domain_summary": {"description": "Unique domains with total citation counts", "total_unique_domains": len(domain_summary), "data": domain_summary}}, indent=2)}

parse the key "domain_summary" and categorize every domain. For each domain produce:

1. domain - the website domain (e.g., "amazon.com")
2. total_citations - the citation count number
3. category - snake_case category identifier
4. category_name - human-readable category name

Category mapping rules:
- Use these predefined categories when applicable:
  * "ecommerce" → "E-commerce"
  * "social_media" → "Social Media"
  * "news" → "News & Media"
  * "review_websites" → "Review & Consumer Sites"
  * "technology" → "Technology Platforms"
  * "healthcare" → "Healthcare Organizations"
  * "medical_devices" → "Medical Device Companies"
  * "health_news" → "Health News & Media"
  * "research" → "Research & Academic"
  * "nonprofit" → "Nonprofit Organizations"
  * "blogs" → "Blogs"
  * "sports" → "Sports & Fitness"
  * "government" → "Government & Public Services"
  * "education" → "Educational Resources"

For domains not fitting above categories, create appropriate snake_case category and corresponding human-readable name.
Every domain must have both category and category_name filled. Return ONLY the structured object with one item per domain."""

    domcat_out = await agent(domcat_prompt, label="categorize-domains", phase="Domains", schema=DOMCAT_SCHEMA)
    domcat_items = (domcat_out and domcat_out.get("items")) or []
    for it in domcat_items:
        category_map[it["domain"]] = it["category"]
        cat_name_map[it["domain"]] = it["category_name"]

analysis = combine_ref_data(matrix_rows, category_map)
reference_html = build_reference_html(url_mapping, cat_name_map, brand, run_time)

# CSV strings (steps 18 + 21)
matrix_csv = "\n".join(
    ["platform,question,domain,citation_count"]
    + [",".join(csv_field(v) for v in [r["platform"], r["question"], r["domain"], r["citation_count"]]) for r in matrix_rows]
)
summary_csv = "\n".join(
    ["domain,total_citations,category,category_name"]
    + [",".join(csv_field(v) for v in [it["domain"], it["total_citations"], it["category"], it["category_name"]]) for it in domcat_items]
)

# Exports are split across two parallel agents to bound per-prompt payload —
# the corpus (and these artifacts) grow with every scheduled run.
export_header = "Write these local files using a python3 script (create parent dirs, overwrite). The payloads below must be written EXACTLY as given. Return the structured object {files: [<paths written>]}."
export_domains_prompt_a = f"""{export_header}

1. WRITE {workspace}/domain_citation_matrix.csv :
{matrix_csv}

2. WRITE {workspace}/domain_summary.csv :
{summary_csv}

3. WRITE {workspace}/domain_reference.html :
{reference_html}"""

export_domains_prompt_b = f"""{export_header}

1. WRITE {workspace}/domain_url_platform_mapping.json :
{json.dumps(url_mapping, indent=2)}

2. WRITE {workspace}/domain_analysis_result.json :
{json.dumps(analysis, indent=2)}"""

payload_bytes = len(export_domains_prompt_a) + len(export_domains_prompt_b)
if payload_bytes > 300000:
    log(f"⚠ domain export payload is {math.floor(payload_bytes / 1000 + 0.5)}KB and grows with corpus size — consider archiving {workspace} and starting a fresh question set.")
dom_exports = await parallel([
    agent(export_domains_prompt_a, label="export-domains-csv-html", phase="Domains", schema=EXPORT_SCHEMA),
    agent(export_domains_prompt_b, label="export-domains-json", phase="Domains", schema=EXPORT_SCHEMA),
])
dom_export = {"files": [f for e in dom_exports if e for f in (e.get("files") or [])]}

# ═══ Phase: Report — step 24 (faithful, genericized) + 25/27 ═════════════════
phase("Report")

# keep the report-agent prompt bounded: drop the bulkiest sections if huge
report_input = analysis
if len(json.dumps(analysis)) > 150000:
    report_input = {**analysis, "by_question": "(omitted for size)", "platform_question_breakdown": "(omitted for size)"}
    log("Analysis JSON > 150KB — report input reduced (by_question + breakdown omitted)")

report_prompt = f"""{json.dumps(report_input, indent=2)}

Generate a comprehensive AEO (Answer Engine Optimization) report based on the domain analysis JSON data provided. This analysis examines how different domains are cited by AI platforms (ChatGPT, Google AI Mode, and Perplexity) when responding to consumer queries about {topic_label}.

## Report Requirements:

### Structure:
1. Executive Summary
2. Domain Performance Rankings
3. Platform-Specific Insights
4. Comprehensive AEO Optimization Strategy

### Section 1 - Executive Summary:
Display these metrics with brief explanations:
- Total platforms: [number] (explain what platforms are analyzed)
- Total questions: [number] (explain these are unique test queries)
- Unique domains: [number] (explain these are distinct websites cited)
- Total platform_question_pairs: [number] (explain this counts unique platform-question combinations where domains appear)
- Total actual_citations: [number] (explain this is the sum of all citation_count values)

**Important**: Add a note explaining the difference between platform_question_occurrences (how many platform-question combinations a domain appears in) and actual_citation_total (the sum of citation counts).

### Section 2 - Domain Performance Rankings:
Create table with these columns:
- Rank
- Domain
- Category
- Platform-Question Occurrences (from platform_question_occurrences field)
- Actual Citations Total (from actual_citation_total field)
- Platform Count (how many different platforms cite this domain)

Show top 15 domains sorted by platform_question_occurrences (highest first).

**Add caption below table**: "Platform-Question Occurrences shows in how many unique (platform, question) pairs the domain was cited. Actual Citations Total shows the cumulative citation count when a domain appears multiple times for the same query."

### Section 3 - Platform Coverage Analysis:

#### 3a. Platform Distribution Table:
Add caption: "*Domains sorted by platform coverage count (high to low)*"

Create table with these columns:
- Domain
- ChatGPT (use ✓ or leave blank)
- Google AI (use ✓ or leave blank)
- Perplexity (use ✓ or leave blank)
- Platform Count (number)
- Platform-Question Occurrences

Sort by platform count (most to least), then by occurrences.

#### 3b. Platform-Specific Metrics:
For each platform, show:
- Total question_occurrences (explain this counts unique questions where domains appear)
- Unique domains cited
- Top 5 domains by question_occurrences

### Section 4 - AEO Optimization Strategy:
Include four tiers:
- Tier 1: Foundation Building (explain citation frequency patterns)
- Tier 2: Platform-Specific Optimization (reference platform_specific_domains data)
- Tier 3: Content Distribution Framework (mention url_reuse patterns)
- Tier 4: Technical Implementation
Each tier must have specific tactics, KPIs based on the metrics, and timelines.

Rules about Key Metrics to Explain:
When using these metrics, always clarify their meaning at the beginning or end of the section in caption, but do not use the raw key name:
- platform_question_occurrences: Number of unique (platform, question) combinations
- actual_citation_total: Sum of all citation_count values for the domain
- question_occurrences: Number of different questions where domain appears (within a platform)
- platform_occurrences: Number of different platforms where domain appears (for a question)
- url_reuse: How many URLs are used for multiple questions
- max_url_reuse: Maximum times a single URL answered different questions

### Writing Rules:
1. No dates/years in titles
2. Use metrics exactly as named in the JSON (platform_question_occurrences, not "citations")
3. Explain what each metric means when first introduced
4. Professional tone for business strategy
5. Pure markdown output without code blocks
6. When showing numbers, add brief inline explanations for clarity

The goal is to help brands understand not just the numbers, but what they mean for optimization strategy. Output ONLY the report markdown — no preamble."""

report_md = (await agent(report_prompt, label="write-aeo-report", phase="Report")) or ""
if not report_md.strip():
    raise RuntimeError("Report agent returned no output.")

run_log_entry = {
    "job_id": job_id,
    "brand": brand,
    "run_time": run_time,
    "questions_total": len(all_questions),
    "scraped_this_run": [
        {
            "question": s["question"],
            "platforms": {r["platform"]: ("ok" if r["status"] == "completed" else ("failed: " + str(r["note"]) if r.get("note") else "failed")) for r in s["results"]},
        }
        for s in scrape_status
    ],
    "platform_health": platform_health,
    "valid_answers_in_corpus": len(valid_corpus),
}

export_report_prompt = f"""Write these local files using a python3 script (create parent dirs). Return the structured object {{files: [<paths written>]}}.

1. WRITE {workspace}/aeo-report.md with EXACTLY this markdown content:
{report_md}

2. UPDATE {workspace}/metadata.json — read it if it exists (shape {{"brand": ..., "runs": [...]}}); if missing or unparseable start from {{"brand": {json.dumps(brand)}, "runs": []}}. Append this run entry to "runs" and write back pretty-printed:
{json.dumps(run_log_entry, indent=2)}

After writing, print the ABSOLUTE path of the report file and include it in files."""

rep_export = await agent(export_report_prompt, label="export-report", phase="Report", schema=EXPORT_SCHEMA)
written_files = [
    *((vis_export and vis_export.get("files")) or []),
    *(dom_export.get("files") or []),
    *((rep_export and rep_export.get("files")) or []),
]
report_path = next((f for f in written_files if f.endswith("aeo-report.md")), f"{workspace}/aeo-report.md")

# ═══ Result (upstream step 27 equivalent) ═════════════════════════════════════
remaining = len(pending) - len([
    b for b in batch
    if (lambda s: s and all(r["status"] == "completed" for r in s["results"]))(
        next((x for x in scrape_status if x["question"] == b["question"]), None)
    )
])
log(f"Done. Report: {report_path} · coverage {len(all_questions) - remaining}/{len(all_questions)} questions · {remaining} still pending")

return {
    "jobId": job_id,
    "brand": brand,
    "brandLink": brand_link,
    "workspace": workspace,
    "questionsTotal": len(all_questions),
    "pendingBeforeRun": len(pending),
    "scrapedThisRun": run_log_entry["scraped_this_run"],
    "platformHealth": platform_health,
    "validAnswersInCorpus": len(valid_corpus),
    "visibility": {
        "brand_visibility_score_percentage": visibility["brand_visibility_score_percentage"],
        "brand_link_visibility_score_percentage": visibility["brand_link_visibility_score_percentage"],
        "per_platform": visibility["platform_visibility_score_percentage_list"],
    },
    "topCompetitors": [
        {"brand": c["brand_name"], "visibility": c["brand_visibility_score_percentage"]}
        for c in sorted(visibility["competitors"], key=lambda a: a["brand_visibility_score_percentage"], reverse=True)[:5]
    ],
    "topDomains": analysis["overall"]["all_domains"][:5],
    "reportPath": report_path,
    "files": written_files,
    "note":
        "Cookie-less mode: platforms may block anonymous queries (see platformHealth). Failed (platform,question) pairs are retried on the next run — schedule this workflow daily to grow coverage. "
        "REQUIREMENT: the gstack browse daemon must already be running in HEADED stealth mode (run /connect-chrome in the main session first) — sandboxed workflow agents cannot start it, and headless mode gets bot-blocked by all three platforms; when the daemon is down or headless the run skips scraping and only recomputes analysis. "
        "Skipped vs the upstream original: S3 uploads and proprietary vendor backend APIs (query log, server competitor list, score submission) — everything is saved to the local workspace instead.",
}
