export const meta = {
  name: 'aeo-content-advisor',
  description: 'AEO Content Advisor — analyze the AI-answer corpus collected by /aeo-data-scientist to find brand content gaps and generate a strategic content roadmap.',
  whenToUse: 'Run with a brand name after /aeo-data-scientist has collected answers. Computes brand visibility / recommendation rates per platform, identifies the questions where the brand is missing, cross-references the domains AI engines trust, and writes an AEO Strategic Audit Report with concrete content ideas.',
  phases: [
    { title: 'Load', detail: 'Read the accumulated AI-answer corpus from the local workspace' },
    { title: 'Audit', detail: 'Mention depth, recommendation rate, top authorities, content gaps (pure JS)' },
    { title: 'Report', detail: 'AEO Strategic Audit Report (faithful upstream prompt)' },
    { title: 'Export', detail: 'Write report + audit JSON to the workspace' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Singula-AI AEO marketing workflow — faithful port of the upstream
// "Agent 4: AEO Content Advisor" (5 nodes) to a
// Claude Code dynamic workflow.
//
//   Upstream node                          → workflow implementation
//   1  Retrive AEO data (py, S3 by job)   → SKIPPED: no upstream S3 — we read agent 3's
//   2  Retrieve AEO - Debug (py)            LOCAL workspace directly (answers/<platform>/*.json),
//   3  Workspace path (py)                  which closes the team.context.job_id seam
//   4  Python script (py, audit builder)  → pure JS port (mention depth, recommend keywords,
//                                            ref blacklist, platform stats, content gaps)
//   5  Create text (gpt-4.1)              → agent (faithful prompt; @Builtin-Today → `date`)
//
// Team-bus seam closed: the original took `team.context.job_id` (published by
// agent 3) and re-downloaded that job's files from the upstream S3. Our agent 3
// port accumulates answers in a persistent per-brand workspace, so this
// workflow just points at the same directory — no job ID, no download.
//
// Input : args = "brand"  OR  { brand, brandLink?, workspace?, outputPath? }
//         workspace defaults to ./demos/aeo-output/aeo-data-scientist/<brand-slug>
//         (must contain answers/<platform>/*.json from /aeo-data-scientist)
// Output: { brand, reportPath, auditPath, overallVisibility, topAuthorities, ... }
// ─────────────────────────────────────────────────────────────────────────────

// ── args normalization ───────────────────────────────────────────────────────
let input = args ?? {}
if (typeof input === 'string') {
  const t = input.trim()
  if (t.startsWith('{')) {
    try { input = JSON.parse(t) } catch (_) { input = { brand: t } }
  } else {
    input = { brand: t }
  }
}
const brand = (input.brand || '').toString().trim()
if (!brand) {
  throw new Error('No brand provided. Pass args like "Cursor" or { brand: "Cursor" } — must match the brand used with /aeo-data-scientist.')
}
// Canonical slugify — keep IDENTICAL across all marketing workflows: the
// CSV/workspace handoff between pipeline stages depends on matching slugs.
const slugify = (s, max = 80) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, max).replace(/-+$/, '') || 'item'
const brandSlug = slugify(brand, 40)
const workspace = (input.workspace || `./demos/aeo-output/aeo-data-scientist/${brandSlug}`).replace(/\/+$/, '')
const reportPath = input.outputPath || `${workspace}/aeo-content-advisor-report.md`
const auditPath = `${workspace}/aeo-content-audit.json`

log(`Brand: "${brand}"  ·  corpus: ${workspace}/answers/  ·  report → ${reportPath}`)

// ── Schemas ──────────────────────────────────────────────────────────────────
const CORPUS_SCHEMA = {
  type: 'object',
  properties: {
    runDate: { type: 'string' },   // YYYY-MM-DD
    answers: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          platform: { type: 'string' },
          question: { type: 'string' },
          status: { type: 'string' },
          answer: { type: 'string' },
          sources: {
            type: 'array',
            items: {
              type: 'object',
              properties: { url: { type: 'string' }, title: { type: 'string' } },
              required: ['url', 'title'],
              additionalProperties: false,
            },
          },
        },
        required: ['platform', 'question', 'status', 'answer', 'sources'],
        additionalProperties: false,
      },
    },
  },
  required: ['runDate', 'answers'],
  additionalProperties: false,
}

const EXPORT_SCHEMA = {
  type: 'object',
  properties: { files: { type: 'array', items: { type: 'string' } } },
  required: ['files'],
  additionalProperties: false,
}

// ── pure-JS port of step-4 (audit builder) ───────────────────────────────────
function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function domainFromUrl(url) {
  if (!url) return ''
  let u = url.trim()
  if (!/^https?:\/\//i.test(u)) u = 'https://' + u
  const m = u.match(/^https?:\/\/([^/?#]+)/i)
  let domain = (m ? m[1] : '').toLowerCase()
  return domain.replace(/^www\./, '').replace(/:\d+$/, '')
}

// step-4 analyze_mention_depth: count \b-bounded mentions; "recommended" when
// any endorsement keyword appears in a text that mentions the brand
const RECOMMEND_KEYWORDS = ['best', 'top', 'recommend', 'reliable', 'superior', 'choice', 'winner', 'excellent']
function analyzeMentionDepth(text, brandName) {
  if (!text || !brandName) return { count: 0, is_recommended: false }
  const lower = text.toLowerCase()
  const mentions = (lower.match(new RegExp(`\\b${escapeRegex(brandName.toLowerCase())}\\b`, 'g')) || []).length
  const isRecommended = mentions > 0 && RECOMMEND_KEYWORDS.some((kw) => lower.includes(kw))
  return { count: mentions, is_recommended: isRecommended }
}

// step-4 extract_detailed_sources (blacklist preserved; domain derived from url
// since our canonical answer files store sources as {url, title})
const REF_BLACKLIST = ['policies.google.com', 'support.google.com', 'blog.google', 'youtube.com/ads']
function extractDetailedSources(sources) {
  const refs = []
  for (const s of sources || []) {
    const domain = domainFromUrl(s.url)
    if (domain && !REF_BLACKLIST.includes(domain)) {
      refs.push({ domain, title: (s.title || '').slice(0, 100), url: s.url || '' })
    }
  }
  return refs
}

// step-4 process_json_file equivalent over a canonical corpus entry
function processEntry(entry, brandName) {
  const brandIntel = analyzeMentionDepth(entry.answer, brandName)
  const refList = extractDetailedSources(entry.sources)
  const brandInCitations = refList.some((ref) => JSON.stringify(ref).toLowerCase().includes(brandName.toLowerCase()))
  return {
    question: entry.question || 'Unknown Question',
    source: entry.platform,
    include_brand: brandIntel.count > 0,
    mention_count: brandIntel.count,
    is_recommended: brandIntel.is_recommended,
    brand_in_citations: brandInCitations,
    ref_list: refList,
  }
}

// step-4 calculate_advanced_stats (same output keys and formatting)
function calculateAdvancedStats(allResults) {
  const platformData = {}
  const domainCounter = {}
  const questionMap = {}
  for (const r of allResults) {
    const p = r.source, q = r.question
    platformData[p] = platformData[p] || { tested: 0, found: 0, recommendations: 0, total_mentions: 0 }
    platformData[p].tested += 1
    if (r.include_brand) {
      platformData[p].found += 1
      platformData[p].total_mentions += r.mention_count
      if (r.is_recommended) platformData[p].recommendations += 1
    }
    ;(questionMap[q] = questionMap[q] || {})[p] = r.include_brand
    for (const ref of r.ref_list) domainCounter[ref.domain] = (domainCounter[ref.domain] || 0) + 1
  }
  const pct = (x) => `${(x * 100).toFixed(1)}%`
  const byPlatform = {}
  for (const [p, s] of Object.entries(platformData)) {
    byPlatform[p] = {
      visibility: pct(s.found / s.tested),
      recommend_rate: pct(s.found > 0 ? s.recommendations / s.found : 0),
      avg_mentions: s.found > 0 ? Math.round((s.total_mentions / s.found) * 100) / 100 : 0,
    }
  }
  const completelyMissing = [], partiallyMissing = []
  for (const [q, pStatus] of Object.entries(questionMap)) {
    const foundIn = Object.entries(pStatus).filter(([, v]) => v).map(([p]) => p)
    const missingIn = Object.entries(pStatus).filter(([, v]) => !v).map(([p]) => p)
    if (!foundIn.length) completelyMissing.push({ question: q, missing: missingIn })
    else if (missingIn.length) partiallyMissing.push({ question: q, found: foundIn, missing: missingIn })
  }
  const totalTested = Object.values(platformData).reduce((a, s) => a + s.tested, 0)
  const totalFound = Object.values(platformData).reduce((a, s) => a + s.found, 0)
  return {
    brand_health: {
      overall_visibility: pct(totalTested > 0 ? totalFound / totalTested : 0),
      top_authorities: Object.entries(domainCounter).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([d]) => d),
    },
    platform_deep_dive: byPlatform,
    content_gaps: {
      high_priority_missing: completelyMissing,
      opportunity_count: completelyMissing.length + partiallyMissing.length,
    },
  }
}

// ═══ Phase: Load — read corpus + current date ════════════════════════════════
phase('Load')

const corpusPrompt = `Read ALL json files under ${workspace}/answers/*/*.json on the local filesystem (use python3; the answers/<platform>/ dir name is the platform). For each file return one entry:
{platform, question: result.question, status: <"status" field>, answer: <result.answer, or result.aiGenerated.formattedContent if answer is missing/empty; truncate to 6000 chars>, sources: [{url, title}] from result.sources (use "" for a missing title; for source items that are plain strings, treat the string as the url)}.
Skip unparseable files. Also run \`date '+%Y-%m-%d'\` and return it as runDate.
Return ONLY the structured object. If there are no files, return {"runDate": <date>, "answers": []}.`

const corpusOut = await agent(corpusPrompt, { label: 'load-corpus', phase: 'Load', schema: CORPUS_SCHEMA })
if (!corpusOut) throw new Error('Corpus loader agent failed.')
const corpus = (corpusOut.answers || []).filter((a) => a.status === 'completed' && a.answer.trim() && a.question.trim())
if (!corpus.length) {
  throw new Error(
    `No completed AI answers found under ${workspace}/answers/. ` +
    `Run /aeo-data-scientist for brand "${brand}" first (it collects the corpus this advisor analyzes), or pass workspace: "<path>" if the data lives elsewhere.`,
  )
}
log(`Corpus: ${corpus.length} completed answers across ${new Set(corpus.map((a) => a.platform)).size} platform(s)`)

// ═══ Phase: Audit — step-4 pure JS ═══════════════════════════════════════════
phase('Audit')

const allResults = corpus.map((entry) => processEntry(entry, brand))
const auditSummary = calculateAdvancedStats(allResults)
const auditJson = {
  success: true,
  brand_name: brand,
  audit_summary: auditSummary,
  raw_results: allResults,
}
log(`Audit: visibility ${auditSummary.brand_health.overall_visibility} · ${auditSummary.content_gaps.high_priority_missing.length} questions with brand fully missing · top authority: ${auditSummary.brand_health.top_authorities[0] || '(none)'}`)

// keep the report prompt bounded: drop per-result ref_list details if huge
let reportInput = auditJson
if (JSON.stringify(auditJson).length > 150000) {
  reportInput = {
    ...auditJson,
    raw_results: allResults.map((r) => ({ ...r, ref_list: r.ref_list.slice(0, 3) })),
  }
  log('Audit JSON > 150KB — ref lists trimmed to 3 per result for the report prompt')
}

// ═══ Phase: Report — step-5 prompt, faithful ═════════════════════════════════
phase('Report')

const reportPrompt = `# AEO Strategy & Content Intelligence - Expert Prompt

## Role
You are a senior AEO (Answer Engine Optimization) Strategist. Your goal is to analyze brand audit data and create a content roadmap that forces AI models (ChatGPT, Google AI, Perplexity) to recognize and recommend ${brand}.

## Task
1. Analyze the provided JSON to identify "Brand Gaps" where ${brand} is missing or not recommended.
2. Cross-reference the "Top Authorities" (domains AI trust) with the missing questions.
3. For each high-priority missing question, generate a strategic content plan.

## Input Data Breakdown
You will receive a JSON containing:
- \`audit_summary\`: Brand health, visibility rates, and \`top_authorities\` (the domains AI currently cites).
- \`raw_results\`: Detailed question-by-question performance including \`is_recommended\` and \`brand_in_citations\`.

## Output Format (Markdown)

# AEO Strategic Audit Report: ${brand}

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
For each question in \`high_priority_missing\` (or grouped by topic):

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
2. **Be Specific:** Content titles must include the ${brand}.
3. **Focus on "Influence":** Don't just suggest blogs; suggest content that provides the *data points* AI models are currently missing.
4. **Tone:** Professional, analytical, and action-oriented.
5. **Time Accuracy**: Today's date is ${corpusOut.runDate} , for any context that implies current date/year, you must use the correct date.

JSON Data:
${JSON.stringify(reportInput, null, 2)}

Output ONLY the report markdown — no preamble.`

const reportMd = (await agent(reportPrompt, { label: 'aeo-strategy-report', phase: 'Report' })) || ''
if (!reportMd.trim()) throw new Error('Report agent returned no output.')

// ═══ Phase: Export ═══════════════════════════════════════════════════════════
phase('Export')

// Split across two parallel agents to bound per-prompt payload — the audit
// JSON grows with the corpus across scheduled runs.
const exportHeader = 'Write this local file using a python3 script (create parent dirs, overwrite). The payload must be written EXACTLY as given. Return the structured object {files: [<ABSOLUTE path written>]}.'
const exps = await parallel([
  () => agent(`${exportHeader}

WRITE ${reportPath} with this markdown content:
${reportMd}`, { label: 'export-report-md', phase: 'Export', schema: EXPORT_SCHEMA }),
  () => agent(`${exportHeader}

WRITE ${auditPath} with this JSON content:
${JSON.stringify(auditJson, null, 2)}`, { label: 'export-audit-json', phase: 'Export', schema: EXPORT_SCHEMA }),
])
const files = exps.filter(Boolean).flatMap((e) => e.files || [])
if (!files.length) files.push(reportPath, auditPath)

log(`Done. Report: ${files.find((f) => f.endsWith('.md')) || reportPath}`)
return {
  brand,
  reportPath: files.find((f) => f.endsWith('.md')) || reportPath,
  auditPath: files.find((f) => f.endsWith('.json')) || auditPath,
  answersAnalyzed: corpus.length,
  overallVisibility: auditSummary.brand_health.overall_visibility,
  platformDeepDive: auditSummary.platform_deep_dive,
  topAuthorities: auditSummary.brand_health.top_authorities.slice(0, 5),
  highPriorityMissing: auditSummary.content_gaps.high_priority_missing.length,
  opportunityCount: auditSummary.content_gaps.opportunity_count,
  note:
    'Reads the local corpus accumulated by /aeo-data-scientist (replaces the upstream S3 job download — team.context.job_id seam closed). ' +
    'The audit improves as the corpus grows: schedule /aeo-data-scientist daily, re-run this advisor whenever you want a fresh roadmap.',
}
