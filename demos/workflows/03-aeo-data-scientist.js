export const meta = {
  name: 'aeo-data-scientist',
  description: 'AEO Full-Stack Data Scientist — ask research questions on ChatGPT, Google AI and Perplexity (cookie-less browser), measure brand visibility vs competitors, analyze cited domains, and write an AEO report.',
  whenToUse: 'Run with a brand name plus research questions (or the CSV produced by /user-prompt-research) to measure how often the brand appears in AI-engine answers, who the competitors are, and which domains get cited. Designed for small daily batches: each run scrapes a few uncovered questions and recomputes scores/report over ALL accumulated data.',
  phases: [
    { title: 'Init', detail: 'Load questions (args or CSV) + scan workspace coverage' },
    { title: 'Scrape', detail: 'Ask each pending question on ChatGPT / Google AI / Perplexity, no login' },
    { title: 'Competitors', detail: 'Extract + clean competitor brands from AI answers' },
    { title: 'Visibility', detail: 'Brand/competitor visibility scores (pure JS port)' },
    { title: 'Domains', detail: 'Citation domain matrix, URL mapping, LLM categorization' },
    { title: 'Report', detail: 'Write the AEO report + run log' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Singula-AI AEO marketing workflow — faithful port of the upstream
// "Agent 3: AEO Full-Stack Data Scientist" (27 nodes)
// to a Claude Code dynamic workflow.
//
//   Upstream node                              → workflow implementation
//   1  Generate job id (py)                   → init agent (date-based id; no RNG in script)
//   2  Workspace path (py, /tmp/<job>)        → persistent ./demos/aeo-output/aeo-data-scientist/<brand-slug>/
//   3  Initialization (py, metadata.json)     → run log appended to metadata.json (export agent)
//   4  Question list (py passthrough)         → args.questions OR /user-prompt-research CSV (closes the
//                                               team-bus seam the upstream left manual)
//   5  Extension scraping (chrome_extension)  → per-question agent + gstack /browse, NO COOKIES
//   6  Save Extension Result (py)             → same agent writes canonical answer JSONs
//   7  1-Gather AI results (loop, Playwright) → same agents (sequential loop, small batch per run)
//   8  Select Data Retrieval Mode (js)        → not needed (always cookie-less browse path)
//   9  1-Write questions to txt (py)          → questions.txt via export agent
//   10 1-Save raw data S3 (py)                → SKIPPED: no upstream S3 — everything stays on local fs
//   11 Submit Query thru API (py)             → SKIPPED (upstream vendor backend)
//   12 2-Parse answers (py, random sample)    → pure JS, DETERMINISTIC sample (no RNG allowed here)
//   13 2-Find all competitors (gpt-4.1)       → agent (faithful prompt)
//   14 2-Reformat competitor list (gpt-4.1)   → agent (faithful prompt, schema-forced list)
//   15 2-Pull competitors from API (py)       → SKIPPED (upstream vendor backend); LLM list is sole source
//   16 2-Calculate Visibility Score (py)      → pure JS port (same math, same output keys)
//   17 2-Submit score thru API (py)           → SKIPPED (upstream vendor backend); saved locally instead
//   18 3-Parse reference to csv (py)          → pure JS port (zero-count rows omitted — step 22
//                                               filtered them out anyway)
//   19 3-Parse domain relations (py)          → pure JS port (url normalization, reuse stats)
//   20 3-Analyze domain categories (ai)       → agent (faithful prompt, schema-forced)
//   21 3-Domain categories 3D CSV (py)        → pure JS builds domain_summary.csv (export agent writes)
//   22 3-Combine ref data (py)                → pure JS port (full analysis JSON shape)
//   23 3-Domain reference list (py→HTML+S3)   → pure JS HTML (condensed), saved locally, no S3/API
//   24 3-Write ref domain report (ai)         → agent (faithful prompt; hardcoded "blood pressure
//                                               monitors" example genericized to args topic)
//   25 3-Save report to file (py)             → export agent
//   26 Upload to S3 (py)                      → SKIPPED: local fs only (user decision)
//   27 Collect results (py)                   → workflow return value
//
// Cookie-less + incremental design (user decision): the browser runs logged
// out (fresh profile, no cookies), and ChatGPT / Google / Perplexity tolerate
// only a few anonymous queries at a time. So each run scrapes at most
// `maxQuestions` not-yet-covered questions (default 3), records per-platform
// success/blocked status, and recomputes all analysis over the FULL
// accumulated corpus. Schedule the workflow daily to grow coverage gradually;
// failed/blocked (platform, question) pairs are retried on later runs.
//
// Browser prerequisites (empirical, 2026-06-09):
//   - The browse daemon must be STARTED FROM THE MAIN SESSION — Claude Code's
//     command sandbox blocks workflow subagents from cold-starting it (hangs
//     on "[browse] Starting server...").
//   - It must run in HEADED stealth mode (/connect-chrome): headless mode is
//     bot-blocked by all three platforms on the very first anonymous query;
//     headed stealth passes all three without any login.
//
// Input : args = { brand (required), brandLink?, topic?, questions?: string[]|string,
//                  questionsCsv?: path, maxQuestions?: 3, platforms?: subset of
//                  ['chatgpt','google-ai','perplexity'], outputDir? }
//   - questions source precedence: args.questions > args.questionsCsv >
//     derived ./demos/aeo-output/user-prompt-research/<topic-slug>-questions.csv
// Output: { jobId, visibility, topCompetitors, topDomains, reportPath, ... }
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
  throw new Error(
    'No brand provided. Pass args like { brand: "Cursor", topic: "ai coding assistant" } ' +
    'or { brand: "Cursor", questions: ["...", "..."] }.',
  )
}
const brandLink = (input.brandLink || '').toString().trim()
const topic = (input.topic || '').toString().trim()
// Canonical slugify — keep IDENTICAL across all marketing workflows: the
// CSV/workspace handoff between pipeline stages depends on matching slugs.
const slugify = (s, max = 80) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, max).replace(/-+$/, '') || 'item'
const brandSlug = slugify(brand, 40)
const workspace = (input.outputDir || `./demos/aeo-output/aeo-data-scientist/${brandSlug}`).replace(/\/+$/, '')
const maxQuestions = Number(input.maxQuestions) > 0 ? Math.floor(Number(input.maxQuestions)) : 3
const ALL_PLATFORMS = ['chatgpt', 'google-ai', 'perplexity']
const platforms = Array.isArray(input.platforms) && input.platforms.length
  ? input.platforms.filter((p) => ALL_PLATFORMS.includes(p))
  : ALL_PLATFORMS
if (!platforms.length) throw new Error(`platforms must be a subset of ${ALL_PLATFORMS.join(', ')}`)

// explicit questions (array or newline string), else CSV path
let explicitQuestions = []
if (Array.isArray(input.questions)) explicitQuestions = input.questions
else if (typeof input.questions === 'string') explicitQuestions = input.questions.split('\n')
explicitQuestions = explicitQuestions.map((q) => q.toString().trim()).filter((q) => q.length > 0)

let csvPath = (input.questionsCsv || '').toString().trim()
if (!explicitQuestions.length && !csvPath && topic) {
  csvPath = `./demos/aeo-output/user-prompt-research/${slugify(topic)}-questions.csv`
}
if (!explicitQuestions.length && !csvPath) {
  throw new Error(
    'No questions provided. Pass questions: [...], questionsCsv: "<path>", or topic: "<keyword>" ' +
    '(topic derives the CSV written by /user-prompt-research). Run /user-prompt-research first if needed.',
  )
}
const topicLabel = topic || `${brand}-related products/services`

log(`Brand: "${brand}"  ·  workspace: ${workspace}  ·  batch: up to ${maxQuestions} question(s) × [${platforms.join(', ')}]  ·  no-login browser mode`)

// ── JSON Schemas for schema-forced agent steps ───────────────────────────────
const INIT_SCHEMA = {
  type: 'object',
  properties: {
    runTime: { type: 'string' },        // "YYYY-MM-DD HH:MM:SS"
    runStamp: { type: 'string' },       // "YYYY_MM_DD_HH_MM_SS"
    daemonHealthy: { type: 'boolean' }, // gstack browse daemon reachable?
    daemonMode: { type: 'string' },     // 'headed' | 'launched' (headless) | 'none'
    csvQuestions: {
      type: 'array',
      items: {
        type: 'object',
        properties: { question: { type: 'string' }, volume: { type: 'string' } },
        required: ['question', 'volume'],
        additionalProperties: false,
      },
    },
    covered: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          platform: { type: 'string' },
          file: { type: 'string' },
          question: { type: 'string' },
          status: { type: 'string' },
        },
        required: ['platform', 'file', 'question', 'status'],
        additionalProperties: false,
      },
    },
  },
  required: ['runTime', 'runStamp', 'daemonHealthy', 'daemonMode', 'csvQuestions', 'covered'],
  additionalProperties: false,
}

// Sandboxed subagents CANNOT cold-start the browse daemon (the sandbox blocks
// the server bind and the CLI hangs on "[browse] Starting server..."). They can
// only talk to an ALREADY-RUNNING daemon. Every browse call therefore goes
// through this hang-proof wrapper, and Init verifies the daemon up-front.
const BROWSE_WRAPPER = `Run every browse command through this hang-proof wrapper (NEVER call the browse binary directly — a cold daemon start hangs forever in this sandbox):
python3 -c "import subprocess,os,sys; r=subprocess.run([os.path.expanduser('~/.claude/skills/gstack/browse/dist/browse')]+sys.argv[1:],capture_output=True,text=True,timeout=90); print(r.stdout); print(r.stderr,file=sys.stderr)" <command> <args...>
If a call raises TimeoutExpired twice in a row, stop using the browser and mark the affected platform(s) failed with note "browse timeout". Never let any single Bash call run longer than ~120 seconds.`

const SCRAPE_SCHEMA = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          platform: { type: 'string', enum: ALL_PLATFORMS },
          status: { type: 'string', enum: ['completed', 'failed'] },
          answerChars: { type: 'integer' },
          sourcesCount: { type: 'integer' },
          note: { type: 'string' },     // '' when fine; 'blocked: login wall', 'captcha', etc.
        },
        required: ['platform', 'status', 'answerChars', 'sourcesCount', 'note'],
        additionalProperties: false,
      },
    },
  },
  required: ['results'],
  additionalProperties: false,
}

const CORPUS_SCHEMA = {
  type: 'object',
  properties: {
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
  required: ['answers'],
  additionalProperties: false,
}

const COMPETITORS_SCHEMA = {
  type: 'object',
  properties: { competitors: { type: 'array', items: { type: 'string' } } },
  required: ['competitors'],
  additionalProperties: false,
}

const DOMCAT_SCHEMA = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          domain: { type: 'string' },
          total_citations: { type: 'integer' },
          category: { type: 'string' },
          category_name: { type: 'string' },
        },
        required: ['domain', 'total_citations', 'category', 'category_name'],
        additionalProperties: false,
      },
    },
  },
  required: ['items'],
  additionalProperties: false,
}

const EXPORT_SCHEMA = {
  type: 'object',
  properties: { files: { type: 'array', items: { type: 'string' } } },
  required: ['files'],
  additionalProperties: false,
}

// ── pure-JS ports of the upstream python transforms ───────────────────────────
function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// step-16 get_domain_from_url / step-18 extract_domain_from_url
function domainFromUrl(url) {
  if (!url) return ''
  let u = url.trim()
  if (!/^https?:\/\//i.test(u)) u = 'https://' + u
  const m = u.match(/^https?:\/\/([^/?#]+)/i)
  let domain = (m ? m[1] : '').toLowerCase()
  domain = domain.replace(/^www\./, '').replace(/:\d+$/, '')
  return domain
}

// step-19 normalize_url
function normalizeUrl(url) {
  let base = url.split('?')[0].split('#')[0]
  if (base.endsWith('/')) base = base.slice(0, -1)
  return base
}

function csvField(v) {
  const s = String(v ?? '')
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

// "10~50" → 30 midpoint for sorting; plain ints pass through
function volumeToNumber(v) {
  const s = String(v ?? '').trim()
  const range = s.match(/^(\d+)\s*~\s*(\d+)$/)
  if (range) return (Number(range[1]) + Number(range[2])) / 2
  const n = Number(s)
  return Number.isFinite(n) ? n : 0
}

// step-12 port: per-platform sample of completed answers, joined as one text.
// Original used random.sample(n/3, max 10); RNG is banned in workflow scripts,
// so we take an evenly-spaced deterministic sample of the same size.
function sampleAnswerText(corpus) {
  const texts = []
  for (const platform of ALL_PLATFORMS) {
    const entries = corpus
      .filter((a) => a.platform === platform && a.status === 'completed' && a.answer.trim())
      .sort((x, y) => x.question.localeCompare(y.question))
    if (!entries.length) continue
    const n = entries.length
    const size = Math.min(Math.max(1, Math.floor(n / 3)), 10, n)
    for (let k = 0; k < size; k++) {
      const idx = Math.floor((k * n) / size)
      texts.push(entries[idx].answer.replace(/[\r\n]+/g, ' '))
    }
  }
  return texts.join('\n')
}

// step-16 extract_ai_content equivalent over our canonical answer entries
function answerContent(entry) {
  const sourceText = entry.sources
    .map((s) => `${domainFromUrl(s.url)} ${s.title || ''}`)
    .join(' ')
  return `${entry.answer} ${sourceText}`
}

function mentionCount(name, content) {
  if (!name || !content) return 0
  const re = new RegExp(`\\b${escapeRegex(name)}\\b`, 'gi')
  return (content.match(re) || []).length
}

// step-16 analyze_brand_visibility port (same output keys)
function analyzeBrandVisibility(corpus, brandName, link) {
  const valid = corpus.filter((a) => a.status === 'completed' && a.answer.trim())
  if (!valid.length) {
    return {
      brand_name: brandName, brand_link: link, brand_logo: '',
      brand_visibility_score_percentage: 0.0,
      brand_link_visibility_score_percentage: 0.0,
      brand_mentions: 0, brand_link_mentions: 0,
      platform_visibility_score_percentage_list: [],
    }
  }
  const linkDomain = domainFromUrl(link)
  let totalScore = 0, totalLinkScore = 0, totalMentions = 0, totalLinkMentions = 0
  const platformStats = {}
  for (const entry of valid) {
    const content = answerContent(entry)
    const mentions = mentionCount(brandName, content)
    const linkMentions = linkDomain ? mentionCount(linkDomain, content) : 0
    totalScore += mentions > 0 ? 1 : 0
    totalLinkScore += linkMentions > 0 ? 1 : 0
    totalMentions += mentions
    totalLinkMentions += linkMentions
    const p = entry.platform
    if (!platformStats[p]) platformStats[p] = { total: 0, withBrand: 0, mentions: 0, withLink: 0, linkMentions: 0 }
    platformStats[p].total += 1
    if (mentions > 0) platformStats[p].withBrand += 1
    platformStats[p].mentions += mentions
    if (linkMentions > 0) platformStats[p].withLink += 1
    platformStats[p].linkMentions += linkMentions
  }
  const round2 = (x) => Math.round(x * 100) / 100
  const platformList = Object.entries(platformStats).map(([name, s]) => ({
    platform_name: name,
    platform_logo: '',
    platform_visibility_score_percentage: round2((s.withBrand / s.total) * 100),
    platform_brand_link_visibility_score_percentage: round2((s.withLink / s.total) * 100),
    platform_brand_mentions: s.mentions,
    platform_brand_link_mentions: s.linkMentions,
  }))
  return {
    brand_name: brandName, brand_link: link, brand_logo: '',
    brand_visibility_score_percentage: round2((totalScore / valid.length) * 100),
    brand_link_visibility_score_percentage: round2((totalLinkScore / valid.length) * 100),
    brand_mentions: totalMentions, brand_link_mentions: totalLinkMentions,
    platform_visibility_score_percentage_list: platformList,
  }
}

// step-18 port: platform×question×domain citation matrix + domain summary.
// Deviation: long-format rows with citation_count === 0 are omitted (step-22
// filtered count>0 anyway, and zero rows explode the CSV as coverage grows).
function buildCitationMatrix(corpus) {
  const matrix = {}
  const allDomains = new Set()
  for (const entry of corpus) {
    if (entry.status !== 'completed') continue
    let q = entry.question
    if (q.length > 100) q = q.slice(0, 97) + '...'
    if (!q) continue
    const domains = entry.sources.map((s) => domainFromUrl(s.url)).filter(Boolean)
    if (!domains.length) continue
    const p = entry.platform
    matrix[p] = matrix[p] || {}
    matrix[p][q] = matrix[p][q] || {}
    for (const d of domains) {
      matrix[p][q][d] = (matrix[p][q][d] || 0) + 1
      allDomains.add(d)
    }
  }
  const rows = []
  for (const p of Object.keys(matrix).sort()) {
    for (const q of Object.keys(matrix[p]).sort()) {
      for (const d of Object.keys(matrix[p][q]).sort()) {
        rows.push({ platform: p, question: q, domain: d, citation_count: matrix[p][q][d] })
      }
    }
  }
  const totals = {}
  for (const r of rows) totals[r.domain] = (totals[r.domain] || 0) + r.citation_count
  const domainSummary = Array.from(allDomains)
    .sort()
    .map((d) => ({ domain: d, total_citations: totals[d] || 0, category: '' }))
    .sort((a, b) => b.total_citations - a.total_citations)
  return { rows, domainSummary, matrix }
}

// step-19 port: domain → url/platform/question mapping with reuse stats
function buildUrlMapping(corpus) {
  const mapping = {}
  for (const entry of corpus) {
    if (entry.status !== 'completed') continue
    let q = entry.question
    if (q.length > 100) q = q.slice(0, 97) + '...'
    if (!q) continue
    for (const s of entry.sources) {
      const url = (s.url || '').trim()
      const domain = domainFromUrl(url)
      if (!domain) continue
      const norm = normalizeUrl(url)
      const m = mapping[domain] = mapping[domain] || { citations: {}, urlUsage: {}, platforms: new Set(), urls: new Set() }
      m.urlUsage[norm] = m.urlUsage[norm] || []
      if (!m.urlUsage[norm].includes(q)) m.urlUsage[norm].push(q)
      const key = `${norm}\u0000${q}`
      if (m.citations[key]) {
        if (!m.citations[key].platforms.includes(entry.platform)) m.citations[key].platforms.push(entry.platform)
      } else {
        m.citations[key] = { url, platform: entry.platform, platforms: [entry.platform], question: q }
      }
      m.platforms.add(entry.platform)
      m.urls.add(norm)
    }
  }
  const final = {}
  for (const domain of Object.keys(mapping).sort()) {
    const m = mapping[domain]
    const citations = Object.values(m.citations).map((c) => ({
      url: c.url,
      platform: c.platforms.length > 1 ? c.platforms.slice().sort().join(', ') : c.platform,
      question: c.question,
    }))
    const reuse = Object.values(m.urlUsage).filter((qs) => qs.length > 1)
    final[domain] = {
      url_citations: citations,
      total_platform_question_unique_citations: citations.length,
      unique_urls: m.urls.size,
      platforms: Array.from(m.platforms).sort(),
      url_reuse: reuse.length,
      max_url_reuse: reuse.length ? Math.max(...reuse.map((qs) => qs.length)) : 0,
    }
  }
  return final
}

// step-22 port: combine matrix + category map into the report-input JSON
function combineRefData(rows, categoryMap) {
  const overall = {}, platformDomains = {}, questionDomains = {}, pqDomains = {}, totals = {}
  const platforms2 = new Set(), questions = new Set(), domains = new Set()
  for (const r of rows) {
    if (r.citation_count <= 0) continue
    platforms2.add(r.platform); questions.add(r.question); domains.add(r.domain)
    totals[r.domain] = (totals[r.domain] || 0) + r.citation_count
    overall[r.domain] = (overall[r.domain] || 0) + 1
    ;(platformDomains[r.platform] = platformDomains[r.platform] || {})[r.domain] =
      (platformDomains[r.platform]?.[r.domain] || 0) + 1
    ;(questionDomains[r.question] = questionDomains[r.question] || {})[r.domain] =
      (questionDomains[r.question]?.[r.domain] || 0) + 1
    const pq = (pqDomains[r.platform] = pqDomains[r.platform] || {})
    ;(pq[r.question] = pq[r.question] || {})[r.domain] = (pq[r.question]?.[r.domain] || 0) + 1
  }
  const cat = (d) => categoryMap[d] || 'unknown'
  const result = {
    summary: {
      total_platforms: platforms2.size,
      total_questions: questions.size,
      total_unique_domains: domains.size,
      total_platform_question_pairs: Object.values(overall).reduce((a, b) => a + b, 0),
      total_actual_citations: Object.values(totals).reduce((a, b) => a + b, 0),
    },
    overall: {
      description: 'Domain occurrence frequency across platform-question combinations',
      all_domains: Object.entries(overall)
        .map(([d, c]) => ({ domain: d, category: cat(d), platform_question_occurrences: c, actual_citation_total: totals[d] }))
        .sort((a, b) => b.platform_question_occurrences - a.platform_question_occurrences),
    },
    by_platform: {},
    by_question: {},
    platform_question_breakdown: {},
  }
  for (const p of Array.from(platforms2).sort()) {
    const pd = platformDomains[p]
    result.by_platform[p] = {
      total_question_occurrences: Object.values(pd).reduce((a, b) => a + b, 0),
      unique_domains: Object.keys(pd).length,
      all_domains: Object.entries(pd)
        .map(([d, c]) => ({ domain: d, category: cat(d), question_occurrences: c }))
        .sort((a, b) => b.question_occurrences - a.question_occurrences),
    }
  }
  for (const q of Array.from(questions).sort()) {
    const qd = questionDomains[q]
    const display = q.length > 100 ? q.slice(0, 100) + '...' : q
    result.by_question[display] = {
      full_question: q,
      total_platform_occurrences: Object.values(qd).reduce((a, b) => a + b, 0),
      unique_domains: Object.keys(qd).length,
      all_domains: Object.entries(qd)
        .map(([d, c]) => ({ domain: d, category: cat(d), platform_occurrences: c }))
        .sort((a, b) => b.platform_occurrences - a.platform_occurrences),
    }
  }
  for (const p of Array.from(platforms2).sort()) {
    const list = []
    for (const [q, dd] of Object.entries(pqDomains[p] || {})) {
      const display = q.length > 80 ? q.slice(0, 80) + '...' : q
      list.push({
        question: display,
        total_domain_occurrences: Object.values(dd).reduce((a, b) => a + b, 0),
        all_domains: Object.entries(dd)
          .map(([d, c]) => ({ domain: d, category: cat(d), occurrences: c }))
          .sort((a, b) => b.occurrences - a.occurrences),
      })
    }
    if (list.length) result.platform_question_breakdown[p] = list
  }
  // comparative analysis
  const domainPlatforms = {}
  for (const [p, pd] of Object.entries(platformDomains)) {
    for (const d of Object.keys(pd)) (domainPlatforms[d] = domainPlatforms[d] || new Set()).add(p)
  }
  const universal = Object.entries(domainPlatforms)
    .filter(([, ps]) => ps.size === platforms2.size)
    .map(([d, ps]) => ({
      domain: d, category: cat(d), platforms: Array.from(ps).sort(),
      total_platform_question_occurrences: overall[d], actual_citation_total: totals[d],
    }))
    .sort((a, b) => b.total_platform_question_occurrences - a.total_platform_question_occurrences)
  const specific = {}
  for (const [d, ps] of Object.entries(domainPlatforms)) {
    if (ps.size === 1) {
      const p = Array.from(ps)[0]
      ;(specific[p] = specific[p] || []).push({ domain: d, category: cat(d), question_occurrences: platformDomains[p][d] })
    }
  }
  for (const p of Object.keys(specific)) specific[p].sort((a, b) => b.question_occurrences - a.question_occurrences)
  result.comparative_analysis = {
    universal_domains: universal,
    platform_specific_domains: specific,
    domain_coverage: {
      domains_in_all_platforms: universal.length,
      domains_in_single_platform: Object.values(specific).reduce((a, l) => a + l.length, 0),
    },
  }
  return result
}

// step-23 port (condensed): static HTML reference list, grouped by category
function buildReferenceHtml(urlMapping, catNameMap, brandName, runTime) {
  const byCategory = {}
  for (const [domain, info] of Object.entries(urlMapping)) {
    const catName = catNameMap[domain] || 'Other'
    ;(byCategory[catName] = byCategory[catName] || []).push({ domain, info })
  }
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  const parts = [
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Domain Reference List</title>',
    '<style>body{font-family:Georgia,serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#222}',
    'h2{border-bottom:2px solid #b5651d;padding-bottom:4px}h3{margin-bottom:2px}',
    '.meta{color:#666;font-size:0.9em}li{margin:2px 0}</style></head><body>',
    `<h1>Domain Reference List</h1><p class="meta">Brand: ${esc(brandName)} · Generated: ${esc(runTime)} · AI-engine citations grouped by domain category</p>`,
  ]
  for (const catName of Object.keys(byCategory).sort()) {
    parts.push(`<h2>${esc(catName)}</h2>`)
    for (const { domain, info } of byCategory[catName].sort((a, b) => b.info.total_platform_question_unique_citations - a.info.total_platform_question_unique_citations)) {
      parts.push(`<h3>${esc(domain)}</h3>`)
      parts.push(`<p class="meta">${info.total_platform_question_unique_citations} citation(s) · ${info.unique_urls} unique URL(s) · platforms: ${esc(info.platforms.join(', '))} · url_reuse: ${info.url_reuse} (max ${info.max_url_reuse})</p>`)
      parts.push('<ul>')
      for (const c of info.url_citations) {
        // citations come from scraped web data — only hyperlink http(s) schemes
        parts.push(/^https?:\/\//i.test(c.url)
          ? `<li><a href="${esc(c.url)}">${esc(normalizeUrl(c.url))}</a> — <em>${esc(c.platform)}</em> — ${esc(c.question)}</li>`
          : `<li>${esc(c.url)} — <em>${esc(c.platform)}</em> — ${esc(c.question)}</li>`)
      }
      parts.push('</ul>')
    }
  }
  parts.push('</body></html>')
  return parts.join('\n')
}

// ═══ Phase: Init — timestamps, dirs, question CSV, coverage scan ═════════════
phase('Init')

const initPrompt = `You are initializing a local AEO research workspace. Do these steps exactly; everything is on the local filesystem relative to the current working directory.

1. Run \`date '+%Y-%m-%d %H:%M:%S'\` → return as runTime. Run \`date '+%Y_%m_%d_%H_%M_%S'\` → return as runStamp.
2. Ensure these directories exist (mkdir -p): ${platforms.map((p) => `${workspace}/answers/${p}`).join('  ')}
3. ${csvPath
    ? `Read the CSV file at "${csvPath}" (columns: Time,Question,Search Volume). Return every row as {question, volume} (volume as the raw string, e.g. "140" or "10~50"). If the file does not exist, return an empty csvQuestions array.`
    : 'No CSV to read — return an empty csvQuestions array.'}
4. Scan ${workspace}/answers/*/*.json — for EACH json file found, parse it and return {platform: <parent dir name>, file: <basename>, question: <result.question or "">, status: <its "status" field or "failed">}. If the directory is empty or missing, return an empty covered array.
5. Check the gstack browse daemon WITHOUT risking a hang — run exactly:
python3 -c "import subprocess,os; r=subprocess.run([os.path.expanduser('~/.claude/skills/gstack/browse/dist/browse'),'status'],capture_output=True,text=True,timeout=20); print(r.stdout)"
→ daemonHealthy = true only if it prints "Status: healthy" within the timeout. On TimeoutExpired, any exception, or other output: daemonHealthy = false (do NOT retry, do NOT try to start the daemon).
→ daemonMode = "headed" if the output contains "Mode: headed", "launched" if it contains "Mode: launched", otherwise "none".

Use bash (ls/date/mkdir) and python3 for parsing. Return ONLY the structured object.`

const init = await agent(initPrompt, { label: 'init-workspace', phase: 'Init', schema: INIT_SCHEMA })
if (!init) throw new Error('Init agent failed — cannot determine workspace state.')
const runTime = init.runTime.trim()
const runStamp = init.runStamp.trim()
const jobId = `AEO_${brandSlug}_${runStamp}`

// Build the master question list (explicit > CSV sorted by volume desc)
let allQuestions
if (explicitQuestions.length) {
  allQuestions = explicitQuestions
} else {
  allQuestions = init.csvQuestions
    .map((r) => ({ q: r.question.trim(), v: volumeToNumber(r.volume) }))
    .filter((r) => r.q.length > 0 && r.q.includes('?'))
    .sort((a, b) => b.v - a.v)
    .map((r) => r.q)
  // CSV merge keeps duplicates across runs — dedupe, keeping highest-volume order
  allQuestions = Array.from(new Set(allQuestions))
}
if (!allQuestions.length) {
  throw new Error(`No usable questions found${csvPath ? ` in ${csvPath}` : ''}. Run /user-prompt-research "${topic || brand}" first, or pass questions explicitly.`)
}

// Coverage: (platform → set of covered question slugs with status completed)
const coveredSet = new Set(
  init.covered
    .filter((c) => c.status === 'completed')
    .map((c) => `${c.platform}\u0000${slugify(c.question)}`),
)
const pending = []   // [{question, slug, missingPlatforms}]
for (const q of allQuestions) {
  const slug = slugify(q)
  const missing = platforms.filter((p) => !coveredSet.has(`${p}\u0000${slug}`))
  if (missing.length) pending.push({ question: q, slug, missingPlatforms: missing })
}
let batch = pending.slice(0, maxQuestions)
if (!init.daemonHealthy && batch.length) {
  log('⚠ gstack browse daemon is NOT running — skipping scraping this run (sandboxed agents cannot start it). Start it from the main session (e.g. run `browse status` via the /browse skill), then re-run.')
  batch = []
} else if (batch.length && init.daemonMode !== 'headed' && !input.allowHeadless) {
  // Empirical (2026-06-09): in HEADLESS mode all three platforms hard-block the
  // anonymous browser on the first query (ChatGPT/Perplexity: Cloudflare loop,
  // Google: sorry-page captcha). Headed stealth mode (/connect-chrome) passes.
  log('⚠ browse daemon is running HEADLESS — all 3 platforms bot-block anonymous headless browsers, so scraping is skipped. Run /connect-chrome in the main session for headed stealth mode (or pass allowHeadless: true to force).')
  batch = []
}
log(`Questions: ${allQuestions.length} total · ${pending.length} with missing platform coverage · scraping ${batch.length} this run (job ${jobId})`)

// ═══ Phase: Scrape — sequential, one agent per question, no cookies ══════════
phase('Scrape')

const scrapeStatus = []  // [{question, results: [{platform, status, note}]}]
for (const item of batch) {
  const scrapePrompt = `You are collecting AI-answer-engine responses for AEO (Answer Engine Optimization) research. You operate a headless browser WITHOUT any login cookies (sandbox environment). Work gently: one question, ${item.missingPlatforms.length} platform(s), at most ONE retry per platform. If a platform shows a login wall, captcha, or bot block, mark it failed — do NOT fight it.

QUESTION: ${item.question}

PLATFORMS TO QUERY (only these): ${item.missingPlatforms.join(', ')}

Browser: the gstack browse daemon is ALREADY RUNNING (verified). ${BROWSE_WRAPPER}
Useful commands: goto <url> · text · snapshot -i · click <sel|@ref> · fill <sel> <val> · type <text> · press Enter · wait --networkidle · links · reload. Take a fresh "text" or "snapshot -i" after each wait to check streaming progress; poll with short waits instead of one long one.

Bot-block handling: if a page shows a Cloudflare/"verify you are human" challenge or a hard login wall, wait ~10s, reload ONCE; if still blocked, mark that platform failed with note "blocked: <reason>" and move on.

Per-platform instructions:
${item.missingPlatforms.includes('chatgpt') ? `- chatgpt: goto https://chatgpt.com — dismiss any consent/"stay logged out" dialogs. Fill the prompt box with the question, submit, then poll (wait a few seconds + re-read text) until the response stops growing. Extract the assistant's full answer as plain text, plus any cited/linked source URLs in the answer.\n` : ''}${item.missingPlatforms.includes('google-ai') ? `- google-ai: goto https://www.google.com/search?udm=50&q=<url-encoded question> (Google AI Mode). If AI Mode is unavailable without login, fall back to a regular search https://www.google.com/search?q=... and use the "AI Overview" block (click "Show more" if present). Extract the AI-generated answer text and the cited source links (url + title). If there is NO AI-generated answer at all, mark failed with note "no AI answer".\n` : ''}${item.missingPlatforms.includes('perplexity') ? `- perplexity: goto https://www.perplexity.ai/search?q=<url-encoded question> (or the homepage search box). Poll until the answer finishes. Extract the answer text and the numbered source citations (url + title).\n` : ''}
After scraping, write ONE json file per platform (including failed ones) using a python3 script. Path: ${workspace}/answers/<platform>/${item.slug}.json (overwrite if present). Get TS_MS via python3 int(time.time()*1000). Exact JSON shape:
{
  "scriptName": "search-<platform>",
  "status": "completed" | "failed",
  "timestamp": TS_MS,
  "result": {
    "question": <the question>,
    "answer": <full plain-text answer, or "" if failed>,
    "answerLength": <len of answer>,
    "sources": [{"url": "...", "title": "..."}],
    "sourcesCount": <len of sources>
  },
  "error": null | "<short reason>"
}
Rules: status "completed" requires a non-empty real answer actually scraped from the platform. NEVER fabricate an answer or sources from your own knowledge — if scraping failed, status is "failed". Keep answer text as-is (do not summarize it).

Return ONLY the structured object: results = one entry per platform with {platform, status, answerChars, sourcesCount, note} (note "" when fine).`

  const out = await agent(scrapePrompt, { label: `ask:${item.slug.slice(0, 40)}`, phase: 'Scrape', schema: SCRAPE_SCHEMA })
  const results = (out && out.results) || item.missingPlatforms.map((p) => ({ platform: p, status: 'failed', answerChars: 0, sourcesCount: 0, note: 'scrape agent failed' }))
  scrapeStatus.push({ question: item.question, results })
  const lineSummary = results.map((r) => `${r.platform}:${r.status === 'completed' ? 'ok' : 'FAIL'}`).join(' ')
  log(`"${item.question.slice(0, 60)}..." → ${lineSummary}`)
}

// per-platform health for this run
const platformHealth = {}
for (const p of platforms) {
  const rs = scrapeStatus.flatMap((s) => s.results).filter((r) => r.platform === p)
  platformHealth[p] = {
    attempted: rs.length,
    completed: rs.filter((r) => r.status === 'completed').length,
    notes: Array.from(new Set(rs.filter((r) => r.note).map((r) => r.note))),
  }
}

// ═══ Load full accumulated corpus (previous runs + this run) ═════════════════
const corpusPrompt = `Read ALL json files under ${workspace}/answers/*/*.json on the local filesystem (use python3; the answers/<platform>/ dir name is the platform). For each file return one entry:
{platform, question: result.question, status: <"status" field>, answer: <result.answer, or result.aiGenerated.formattedContent if answer is missing/empty; truncate to 6000 chars>, sources: [{url, title}] from result.sources (use "" for a missing title; for source items that are plain strings, treat the string as the url)}.
Skip unparseable files. Return ONLY the structured object. If there are no files, return {"answers": []}.`

const corpusOut = await agent(corpusPrompt, { label: 'load-corpus', phase: 'Scrape', schema: CORPUS_SCHEMA })
const corpus = ((corpusOut && corpusOut.answers) || []).filter((a) => a.question && a.question.trim())
const validCorpus = corpus.filter((a) => a.status === 'completed' && a.answer.trim())
log(`Corpus: ${corpus.length} answer files, ${validCorpus.length} valid (completed) answers`)
if (!validCorpus.length) {
  throw new Error(
    `No successful answers in the corpus (platform status: ${JSON.stringify(platformHealth)}; browse daemon healthy: ${init.daemonHealthy}). ` +
    'Nothing to analyze. If the daemon was down, start it from the main session (any /browse command) and re-run. ' +
    'If platforms blocked the anonymous browser, try again later, reduce maxQuestions, or import cookies via /setup-browser-cookies.',
  )
}

// ═══ Phase: Competitors — steps 12/13/14 (15 skipped: upstream API) ═══════════
phase('Competitors')

const sampledAnswers = sampleAnswerText(corpus)
const questionsJoined = allQuestions.join('\n')

// step-13 prompt, faithful
const findCompetitorsPrompt = `Task: Extract competitor brand names from the AI response.

Variables:
QUESTIONS = ${questionsJoined}
AI_ANSWER = ${sampledAnswers}

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
If none found, return empty string.`

const rawCompetitors = (await agent(findCompetitorsPrompt, { label: 'find-competitors', phase: 'Competitors' })) || ''

// step-14 prompt, faithful (output schema-forced instead of one-per-line text)
const reformatPrompt = `Inputs
RAW_LIST = ${rawCompetitors}
MY_BRAND = ${brand}

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

Return ONLY the structured object: {competitors: [...]}.`

const compOut = await agent(reformatPrompt, { label: 'clean-competitors', phase: 'Competitors', schema: COMPETITORS_SCHEMA })
const competitors = Array.from(new Set(((compOut && compOut.competitors) || []).map((c) => c.trim()).filter((c) => c && c.toLowerCase() !== brand.toLowerCase())))
log(`Competitors found in AI answers: ${competitors.length ? competitors.join(', ') : '(none)'}`)

// ═══ Phase: Visibility — step 16 pure-JS port (step 17 skipped) ══════════════
phase('Visibility')

const visibility = analyzeBrandVisibility(corpus, brand, brandLink)
visibility.competitors = competitors.map((c) => {
  const r = analyzeBrandVisibility(corpus, c, '')
  return {
    brand_name: c,
    brand_logo: '',
    brand_visibility_score_percentage: r.brand_visibility_score_percentage,
    platform_visibility_score_percentage_list: r.platform_visibility_score_percentage_list,
  }
})
log(`Brand visibility: ${visibility.brand_visibility_score_percentage}% of ${validCorpus.length} valid answers mention "${brand}"`)

// visibility_history.csv row (trend across scheduled runs)
const platPct = (p) => {
  const e = visibility.platform_visibility_score_percentage_list.find((x) => x.platform_name === p)
  return e ? e.platform_visibility_score_percentage : ''
}
const historyHeader = 'Time,Job,Valid Answers,Brand %,Link %,chatgpt %,google-ai %,perplexity %'
const historyRow = [runTime, jobId, validCorpus.length, visibility.brand_visibility_score_percentage, visibility.brand_link_visibility_score_percentage, platPct('chatgpt'), platPct('google-ai'), platPct('perplexity')].map(csvField).join(',')

const exportVisibilityPrompt = `Write these local files using a python3 script (create parent dirs; overwrite unless told to append). Return the structured object {files: [<paths written>]}.

1. WRITE ${workspace}/visibility_score.json with EXACTLY this JSON content:
${JSON.stringify(visibility, null, 2)}

2. APPEND to ${workspace}/visibility_history.csv: if the file does not exist, first write the header line "${historyHeader}". Then append this row:
${historyRow}

3. WRITE ${workspace}/questions.txt — one question per line:
${allQuestions.join('\n')}`

const visExport = await agent(exportVisibilityPrompt, { label: 'export-visibility', phase: 'Visibility', schema: EXPORT_SCHEMA })

// ═══ Phase: Domains — steps 18-23 (pure JS + one LLM categorization) ═════════
phase('Domains')

const { rows: matrixRows, domainSummary } = buildCitationMatrix(corpus)
const urlMapping = buildUrlMapping(corpus)
log(`Citations: ${matrixRows.length} (platform,question,domain) rows across ${domainSummary.length} unique domains`)

// step-20 prompt, faithful (input = step-18 domain_summary), schema-forced
let categoryMap = {}, catNameMap = {}, domcatItems = []
if (domainSummary.length) {
  const domcatPrompt = `From the json input:
${JSON.stringify({ domain_summary: { description: 'Unique domains with total citation counts', total_unique_domains: domainSummary.length, data: domainSummary } }, null, 2)}

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
Every domain must have both category and category_name filled. Return ONLY the structured object with one item per domain.`

  const domcatOut = await agent(domcatPrompt, { label: 'categorize-domains', phase: 'Domains', schema: DOMCAT_SCHEMA })
  domcatItems = (domcatOut && domcatOut.items) || []
  for (const it of domcatItems) {
    categoryMap[it.domain] = it.category
    catNameMap[it.domain] = it.category_name
  }
}

const analysis = combineRefData(matrixRows, categoryMap)
const referenceHtml = buildReferenceHtml(urlMapping, catNameMap, brand, runTime)

// CSV strings (steps 18 + 21)
const matrixCsv = ['platform,question,domain,citation_count']
  .concat(matrixRows.map((r) => [r.platform, r.question, r.domain, r.citation_count].map(csvField).join(',')))
  .join('\n')
const summaryCsv = ['domain,total_citations,category,category_name']
  .concat(domcatItems.map((it) => [it.domain, it.total_citations, it.category, it.category_name].map(csvField).join(',')))
  .join('\n')

// Exports are split across two parallel agents to bound per-prompt payload —
// the corpus (and these artifacts) grow with every scheduled run.
const exportHeader = 'Write these local files using a python3 script (create parent dirs, overwrite). The payloads below must be written EXACTLY as given. Return the structured object {files: [<paths written>]}.'
const exportDomainsPromptA = `${exportHeader}

1. WRITE ${workspace}/domain_citation_matrix.csv :
${matrixCsv}

2. WRITE ${workspace}/domain_summary.csv :
${summaryCsv}

3. WRITE ${workspace}/domain_reference.html :
${referenceHtml}`

const exportDomainsPromptB = `${exportHeader}

1. WRITE ${workspace}/domain_url_platform_mapping.json :
${JSON.stringify(urlMapping, null, 2)}

2. WRITE ${workspace}/domain_analysis_result.json :
${JSON.stringify(analysis, null, 2)}`

const payloadBytes = exportDomainsPromptA.length + exportDomainsPromptB.length
if (payloadBytes > 300000) {
  log(`⚠ domain export payload is ${Math.round(payloadBytes / 1000)}KB and grows with corpus size — consider archiving ${workspace} and starting a fresh question set.`)
}
const domExports = await parallel([
  () => agent(exportDomainsPromptA, { label: 'export-domains-csv-html', phase: 'Domains', schema: EXPORT_SCHEMA }),
  () => agent(exportDomainsPromptB, { label: 'export-domains-json', phase: 'Domains', schema: EXPORT_SCHEMA }),
])
const domExport = { files: domExports.filter(Boolean).flatMap((e) => e.files || []) }

// ═══ Phase: Report — step 24 (faithful, genericized) + 25/27 ═════════════════
phase('Report')

// keep the report-agent prompt bounded: drop the bulkiest sections if huge
let reportInput = analysis
if (JSON.stringify(analysis).length > 150000) {
  reportInput = { ...analysis, by_question: '(omitted for size)', platform_question_breakdown: '(omitted for size)' }
  log('Analysis JSON > 150KB — report input reduced (by_question + breakdown omitted)')
}

const reportPrompt = `${JSON.stringify(reportInput, null, 2)}

Generate a comprehensive AEO (Answer Engine Optimization) report based on the domain analysis JSON data provided. This analysis examines how different domains are cited by AI platforms (ChatGPT, Google AI Mode, and Perplexity) when responding to consumer queries about ${topicLabel}.

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

The goal is to help brands understand not just the numbers, but what they mean for optimization strategy. Output ONLY the report markdown — no preamble.`

const reportMd = (await agent(reportPrompt, { label: 'write-aeo-report', phase: 'Report' })) || ''
if (!reportMd.trim()) throw new Error('Report agent returned no output.')

const runLogEntry = {
  job_id: jobId,
  brand,
  run_time: runTime,
  questions_total: allQuestions.length,
  scraped_this_run: scrapeStatus.map((s) => ({
    question: s.question,
    platforms: Object.fromEntries(s.results.map((r) => [r.platform, r.status === 'completed' ? 'ok' : `failed${r.note ? `: ${r.note}` : ''}`])),
  })),
  platform_health: platformHealth,
  valid_answers_in_corpus: validCorpus.length,
}

const exportReportPrompt = `Write these local files using a python3 script (create parent dirs). Return the structured object {files: [<paths written>]}.

1. WRITE ${workspace}/aeo-report.md with EXACTLY this markdown content:
${reportMd}

2. UPDATE ${workspace}/metadata.json — read it if it exists (shape {"brand": ..., "runs": [...]}); if missing or unparseable start from {"brand": ${JSON.stringify(brand)}, "runs": []}. Append this run entry to "runs" and write back pretty-printed:
${JSON.stringify(runLogEntry, null, 2)}

After writing, print the ABSOLUTE path of the report file and include it in files.`

const repExport = await agent(exportReportPrompt, { label: 'export-report', phase: 'Report', schema: EXPORT_SCHEMA })
const writtenFiles = [
  ...((visExport && visExport.files) || []),
  ...((domExport && domExport.files) || []),
  ...((repExport && repExport.files) || []),
]
const reportPath = writtenFiles.find((f) => f.endsWith('aeo-report.md')) || `${workspace}/aeo-report.md`

// ═══ Result (upstream step 27 equivalent) ═════════════════════════════════════
const remaining = pending.length - batch.filter((b) => {
  const s = scrapeStatus.find((x) => x.question === b.question)
  return s && s.results.every((r) => r.status === 'completed')
}).length
log(`Done. Report: ${reportPath} · coverage ${allQuestions.length - remaining}/${allQuestions.length} questions · ${remaining} still pending`)

return {
  jobId,
  brand,
  brandLink,
  workspace,
  questionsTotal: allQuestions.length,
  pendingBeforeRun: pending.length,
  scrapedThisRun: runLogEntry.scraped_this_run,
  platformHealth,
  validAnswersInCorpus: validCorpus.length,
  visibility: {
    brand_visibility_score_percentage: visibility.brand_visibility_score_percentage,
    brand_link_visibility_score_percentage: visibility.brand_link_visibility_score_percentage,
    per_platform: visibility.platform_visibility_score_percentage_list,
  },
  topCompetitors: visibility.competitors
    .slice()
    .sort((a, b) => b.brand_visibility_score_percentage - a.brand_visibility_score_percentage)
    .slice(0, 5)
    .map((c) => ({ brand: c.brand_name, visibility: c.brand_visibility_score_percentage })),
  topDomains: analysis.overall.all_domains.slice(0, 5),
  reportPath,
  files: writtenFiles,
  note:
    'Cookie-less mode: platforms may block anonymous queries (see platformHealth). Failed (platform,question) pairs are retried on the next run — schedule this workflow daily to grow coverage. ' +
    'REQUIREMENT: the gstack browse daemon must already be running in HEADED stealth mode (run /connect-chrome in the main session first) — sandboxed workflow agents cannot start it, and headless mode gets bot-blocked by all three platforms; when the daemon is down or headless the run skips scraping and only recomputes analysis. ' +
    'Skipped vs the upstream original: S3 uploads and proprietary vendor backend APIs (query log, server competitor list, score submission) — everything is saved to the local workspace instead.',
}
