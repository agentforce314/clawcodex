export const meta = {
  name: 'user-prompt-research',
  description: 'AEO User Prompt Research — discover what customers really ask about a company/product/keyword, score the questions by search volume, and export to CSV.',
  whenToUse: 'Run with a company name, product name, or search keyword to mine real user questions and intent for Answer Engine Optimization (AEO). Produces a Time/Question/Search Volume CSV.',
  phases: [
    { title: 'Search', detail: 'Reddit + Google search (parallel) for the keyword' },
    { title: 'Research', detail: 'Keyword research doc + N condensed intent signals' },
    { title: 'Questions', detail: 'Rewrite intent signals into natural user questions' },
    { title: 'Keywords', detail: 'Extract core/semantic/longtail keywords per question' },
    { title: 'Volume', detail: 'Estimate monthly search volume for semantic keywords' },
    { title: 'Export', detail: 'Compute per-question volume and write the CSV' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// Singula-AI AEO marketing workflow — faithful port of the upstream
// "Agent 2: User Prompt research V2" (12 nodes) to
// a Claude Code dynamic workflow.
//
//   Upstream node                          → workflow implementation
//   1  Search reddit (search_reddit)      → agent + WebSearch (site:reddit.com)
//   2  Search Google API (SerpAPI)        → agent + WebSearch (schema: organic + PAA)
//   3  Google search results (py)         → pure JS formatter (formatOrganic)
//   4  People also search (py)            → pure JS formatter (formatPeopleAlsoAsk)
//   5  Keyword Research Docs (gpt-4.1)    → agent + WebSearch / WebFetch(guide URL)
//   6  Asking questions (gpt-4.1-mini)    → agent
//   7  Question -> Keywords (gpt-4.1)     → agent (schema: per-question keywords)
//   8  Combine Question Keywords (py)     → pure JS (combineSemantic)
//   9  Fetch Keywords Search Volume (py)  → agent estimate (DataForSEO unavailable)
//   10 Calc Question Search Volume (py)   → pure JS (calcQuestionVolume)
//   11 Export questions (py, writes CSV)  → agent + Write / Bash (merge + write CSV)
//   12 Csv file path (js)                 → workflow return value
//
// Input : args = "keyword"  OR  { keyword|productKeyword|topic, count?, guideUrl?, outputDir? }
// Output: { csvPath, keyword, questionsCount, ... }
// ─────────────────────────────────────────────────────────────────────────────

// args may arrive as an object, a bare keyword string, or — depending on how the
// command is invoked — a JSON-encoded string. Normalize all three.
let input = args ?? {}
if (typeof input === 'string') {
  const t = input.trim()
  if (t.startsWith('{') || t.startsWith('[')) {
    try { input = JSON.parse(t) } catch (_) { /* treat as a plain keyword */ }
  }
}
const keyword = (typeof input === 'string'
  ? input
  : (input.keyword || input.productKeyword || input.topic || '')).toString().trim()
if (!keyword) {
  throw new Error(
    'No keyword provided. Pass a company name, product name, or search keyword via args — ' +
    'e.g. args: "AI coding assistant"  or  args: { keyword: "AI coding assistant", count: 50 }',
  )
}
const count = (typeof input === 'object' && Number(input.count)) ? Number(input.count) : 50
// The upstream original fetched its Keyword Research Guide from a
// third-party CDN on every run — a third-party remote-instruction
// dependency seed data must not ship. The guide content is VENDORED below
// instead (fetched verbatim 2026-06-09; human-readable copy:
// keyword-research-guide.md next to this file in the seed folder).
// Pass guideUrl explicitly to fetch a different guide at runtime.
const guideUrl = (typeof input === 'object' && input.guideUrl) ? input.guideUrl : ''

const KEYWORD_RESEARCH_GUIDE = `Purpose of this step:
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
No bullet points, no numbering, no extra explanation.`
const outputDir = (typeof input === 'object' && input.outputDir)
  ? input.outputDir
  : './demos/aeo-output/user-prompt-research'
// Canonical slugify — keep IDENTICAL across all marketing workflows: the
// CSV/workspace handoff between pipeline stages depends on matching slugs.
const slugify = (s, max = 80) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, max).replace(/-+$/, '') || 'item'
const slug = slugify(keyword)
const csvPath = `${outputDir}/${slug}-questions.csv`

log(`Keyword: "${keyword}"  ·  target signals/questions: ${count}  ·  output: ${csvPath}`)

// ── JSON Schemas for the structured (schema-forced) agent steps ──────────────
const GOOGLE_SCHEMA = {
  type: 'object',
  properties: {
    organic_results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          snippet: { type: 'string' },
          link: { type: 'string' },
        },
        required: ['title', 'snippet', 'link'],
        additionalProperties: false,
      },
    },
    related_questions: {
      type: 'array',
      items: {
        type: 'object',
        properties: { question: { type: 'string' } },
        required: ['question'],
        additionalProperties: false,
      },
    },
  },
  required: ['organic_results', 'related_questions'],
  additionalProperties: false,
}

const KEYWORDS_SCHEMA = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string' },
          core: { type: 'string' },
          semantic: { type: 'array', items: { type: 'string' } },
          longtail: { type: 'array', items: { type: 'string' } },
        },
        required: ['question', 'core', 'semantic', 'longtail'],
        additionalProperties: false,
      },
    },
  },
  required: ['items'],
  additionalProperties: false,
}

const VOLUME_SCHEMA = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          keyword: { type: 'string' },
          search_volume: { type: 'integer' },
        },
        required: ['keyword', 'search_volume'],
        additionalProperties: false,
      },
    },
  },
  required: ['items'],
  additionalProperties: false,
}

// ── Pure-JS ports of the upstream python transforms (steps 3, 4, 8, 10) ───────
function formatOrganic(organic) {
  if (!organic || !organic.length) return 'No organic results found.'
  const lines = ['=== Search Results ===\n']
  organic.forEach((item, i) => {
    lines.push(`[${i + 1}] ${item.title || ''}`)
    lines.push(`Source: ${item.link || ''}`)
    lines.push(`${item.snippet || ''}\n`)
  })
  return lines.join('\n')
}

function formatPeopleAlsoAsk(questions) {
  if (!questions || !questions.length) return "No 'People also ask' questions found."
  const lines = ['=== People Also Search ===\n']
  questions.forEach((q, i) => lines.push(`Q${i + 1}: ${q.question || ''}`))
  return lines.join('\n')
}

function combineSemantic(items) {
  const set = new Set()
  for (const it of items) {
    for (const kw of (it.semantic || [])) {
      if (kw && kw.trim()) set.add(kw.trim())
    }
  }
  return Array.from(set).sort()
}

// Port of step-10 script.py: weighted volume of the top-3 semantic keywords
// per question (weights 0.5/0.3/0.2, normalized); < 50 → "10~50".
function calcQuestionVolume(items, volumeItems) {
  const volMap = new Map()
  for (const v of volumeItems) {
    if (v && v.keyword) volMap.set(v.keyword.toLowerCase(), v.search_volume)
  }
  const weights = [0.5, 0.3, 0.2]
  const results = {}
  for (const content of items) {
    const question = content.question || ''
    const semantic = content.semantic || []
    const found = []
    const missing = []
    for (const kw of semantic) {
      const key = (kw || '').toLowerCase()
      const vol = volMap.has(key) ? volMap.get(key) : null
      if (vol !== null && vol !== undefined) found.push([kw, vol])
      else missing.push(kw)
    }
    found.sort((a, b) => b[1] - a[1])
    const top = found.slice(0, 3)
    let sumW = weights.slice(0, top.length).reduce((a, b) => a + b, 0)
    if (sumW === 0) sumW = 1
    let weighted = 0
    top.forEach((t, idx) => { weighted += t[1] * weights[idx] })
    const finalVol = weighted / sumW
    results[question] = {
      top_semantic_keywords: top.map((t) => t[0]),
      search_volume: finalVol < 50 ? '10~50' : Math.round(finalVol),
      missing_semantic_keywords: missing,
    }
  }
  return results
}

// ═══ Steps 1 + 2 : Reddit + Google search (parallel) ═════════════════════════
phase('Search')

const redditPrompt = `You are researching what real users say about a topic on Reddit, for SEO/AEO keyword research.

Topic / search keywords: "${keyword}"

Use web search (try queries like: site:reddit.com ${keyword}  ·  ${keyword} reddit  ·  "${keyword}" reddit recommendations / experiences / vs / alternatives) to find the most relevant Reddit threads and discussions.

Return a concise PLAIN-TEXT digest of the most relevant Reddit findings (aim for 10-20 threads). For each thread: put the subreddit + thread title on one line, then a 1-3 sentence summary of what users are asking, recommending, complaining about, comparing, or deciding between. Capture real user language, pain points, trade-offs, constraints, and comparison moments. No preamble and no markdown headers.`

const googlePrompt = `You are gathering Google search results for SEO/AEO research — simulate scraping the Google SERP for this query.

Query: "${keyword}"

Use web search to gather:
1) organic_results: the top ~10 organic web results. For each: title, snippet (a 1-2 sentence description of the page — REMOVE any raw URLs from the snippet text), and link (the result URL).
2) related_questions: the "People also ask" style questions related to this query (aim for 8-10). Each item is just the question text.

Return ONLY the structured object.`

const searchResults = await parallel([
  () => agent(redditPrompt, { label: 'reddit-search', phase: 'Search' }),
  () => agent(googlePrompt, { label: 'google-search', phase: 'Search', schema: GOOGLE_SCHEMA }),
])
const reddit = searchResults[0] || 'No Reddit results available.'
const google = searchResults[1] || { organic_results: [], related_questions: [] }

// ═══ Steps 3 + 4 : format the SERP (pure JS) ═════════════════════════════════
const googleResultsText = formatOrganic(google.organic_results)
const peopleAlsoAskText = formatPeopleAlsoAsk(google.related_questions)
log(`Google: ${google.organic_results?.length || 0} organic results, ${google.related_questions?.length || 0} related questions`)

// ═══ Step 5 : Keyword Research Docs ══════════════════════════════════════════
phase('Research')

const researchPrompt = `Input:
${guideUrl
    ? `- Keyword Research Guide: ${guideUrl}  (fetch this with WebFetch; if it is unavailable, fall back to standard SEO/AEO keyword-research best practices)`
    : `- Keyword Research Guide (apply this methodology):
<keyword_research_guide>
${KEYWORD_RESEARCH_GUIDE}
</keyword_research_guide>
  (Note: the guide's "Output format" section describes question generation in a later step — for THIS task, follow the Task and output requirements stated below.)`}
- Original search keywords: [ ${keyword} ]
- Google search results:
${googleResultsText}
- Google "people also search":
${peopleAlsoAskText}
- Reddit search results:
${reddit}
- In addition, use web search as much as possible to obtain more relevant information.
- Today: determine the current date (run \`date +%Y-%m-%d\` if helpful).

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
- In the "Condensed Intent Signals" section, produce exactly ${count} distinct intent signals, one per line.`

const researchDoc = await agent(researchPrompt, { label: 'keyword-research-doc', phase: 'Research' })
if (!researchDoc) throw new Error('Step 5 (Keyword Research Docs) returned no output.')

// ═══ Step 6 : Asking questions ═══════════════════════════════════════════════
phase('Questions')

const questionsPrompt = `You are rewriting condensed intent signals into natural, user-facing questions.

Context:
The intent signals are shorthand labels derived from deeper research.
They are not final questions and must be expressed as real queries.

Inputs:
1) Full research context (the "Condensed Intent Signals" section is at the end):
${researchDoc}

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
One question per line. No numbering. No bullets. No extra text. Produce ${count} questions.`

const questionsRaw = await agent(questionsPrompt, { label: 'asking-questions', phase: 'Questions' })
const questionLines = (questionsRaw || '')
  .split('\n')
  .map((s) => s.replace(/^\s*[-*\d.)\]]+\s*/, '').trim())
  .filter((s) => s.length > 0 && s.includes('?'))
if (!questionLines.length) throw new Error('Step 6 (Asking questions) produced no questions.')
log(`Generated ${questionLines.length} questions`)

// ═══ Step 7 : Question -> Keywords ═══════════════════════════════════════════
phase('Keywords')

const keywordsPrompt = `You are an SEO expert. Extract keywords from each of the following questions.

Questions (one per line):
${questionLines.join('\n')}

Rules:
1. Identify ONE core intent keyword.
2. Identify up to 5 semantic-equivalent keywords (same user intent).
3. Identify up to 5 long-tail supporting keywords.
4. Remove stopwords.
5. Do NOT invent brands.

Return one object per question. Each object must include the EXACT original question text (verbatim) plus its core, semantic (array), and longtail (array) keywords.`

const kwOut = await agent(keywordsPrompt, { label: 'question-to-keywords', phase: 'Keywords', schema: KEYWORDS_SCHEMA })
const kwItems = (kwOut && kwOut.items) || []
if (!kwItems.length) throw new Error('Step 7 (Question -> Keywords) produced no keyword items.')

// ═══ Step 8 : Combine semantic keywords (pure JS) ════════════════════════════
const semanticList = combineSemantic(kwItems)
log(`Combined ${semanticList.length} unique semantic keywords across ${kwItems.length} questions`)

// ═══ Step 9 : Fetch Keywords Search Volume (estimated) ═══════════════════════
phase('Volume')

const volumePrompt = `You are an SEO keyword research analyst. Estimate the approximate average MONTHLY Google search volume (US market) for each keyword below.

Keywords (one per line):
${semanticList.join('\n')}

Notes:
- Live DataForSEO / Google Ads data is not available here, so produce reasoned estimates based on your knowledge and (optionally) web-search signals about popularity and competition.
- search_volume must be an integer (average monthly searches). Use 0 only when there is essentially no search demand.

Return one object per keyword: keyword (exact text as given) and search_volume (integer).`

const volOut = await agent(volumePrompt, { label: 'search-volume', phase: 'Volume', schema: VOLUME_SCHEMA })
const volItems = (volOut && volOut.items) || []
log(`Estimated search volume for ${volItems.length} keywords`)

// ═══ Step 10 : Calc per-question search volume (pure JS) ═════════════════════
const questionVolumes = calcQuestionVolume(kwItems, volItems)
const volByQ = {}
for (const [q, info] of Object.entries(questionVolumes)) {
  volByQ[q.trim().toLowerCase()] = info.search_volume
}
const rows = questionLines.map((q) => [q, volByQ[q.trim().toLowerCase()] ?? 0])

// ═══ Step 11 : Export questions to CSV (agent writes the file) ═══════════════
phase('Export')

const exportPrompt = `Write research questions to a CSV file, MERGING with any existing file at the same path. Follow this exactly.

Output CSV path (relative to the current working directory): ${csvPath}

CSV columns (exact header line): Time,Question,Search Volume

New rows to add — JSON array of [question, search_volume] pairs:
${JSON.stringify(rows, null, 2)}

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

Implement this by writing a small Python 3 script to a temp file and running it with python3 (do NOT rely on any pre-injected variables). After the file is written, print ONLY the absolute path to the CSV file as the final line of your output — nothing else.`

const exportOut = await agent(exportPrompt, { label: 'export-csv', phase: 'Export' })
const outLines = (exportOut || '').split('\n').map((s) => s.trim()).filter(Boolean)
const finalPath = outLines.reverse().find((l) => l.endsWith('.csv')) || csvPath

// ═══ Step 12 : CSV file path (workflow return value) ═════════════════════════
log(`Done. CSV written to: ${finalPath}`)
return {
  keyword,
  csvPath: finalPath,
  questionsCount: questionLines.length,
  semanticKeywordsCount: semanticList.length,
  intentSignalsTarget: count,
  organicResults: google.organic_results?.length || 0,
  relatedQuestions: google.related_questions?.length || 0,
  note: 'Search volumes are model-estimated (DataForSEO / Google Ads not available in Claude Code). Reddit + Google SERP gathered via WebSearch instead of the upstream SerpAPI / Reddit integrations.',
}
