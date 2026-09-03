import { useState } from "react";

const SPEC = {
  meta: { version: "0.6", updated: "2026-09-03" },
  stats: [
    { label: "Brain cost/mo", value: "$0", color: "#16a34a" },
    { label: "Research cost/mo", value: "$0", color: "#16a34a" },
    { label: "Payment hub", value: "Payoneer", color: "#1d4ed8" },
    { label: "Death line", value: "$10 debt", color: "#dc2626" },
  ],
  sections: [
    {
      id: "brain",
      icon: "🧠",
      title: "Brain stack",
      status: "defined",
      rows: [
        { k: "Primary (heavy + research)", v: "NVIDIA NIM · 40 RPM · not credit-based" },
        { k: "Speed layer", v: "Groq · 30 RPM · 14,400 req/day" },
        { k: "Volume layer", v: "Gemini Flash · 1M tokens/day · 1,500 req/day" },
        { k: "Large budget fallback", v: "Mistral · 1B tokens/month free" },
        { k: "Model variety", v: "OpenRouter :free · 28+ models · no card" },
        { k: "Edge fallback", v: "Cloudflare Workers AI · 10K neurons/day" },
        { k: "Router layer", v: "FreeLLMAPI / freelm Python package" },
        { k: "Total monthly tokens", v: "~7.4B combined (FreeLLMAPI estimate)" },
        { k: "NVIDIA key fact", v: "Rate-limited only — no credit budget, runs forever" },
      ],
    },
    {
      id: "research",
      icon: "🔍",
      title: "Research tools",
      status: "new",
      rows: [
        { k: "Web search", v: "DuckDuckGo · no API key · 30 RPM · $0 forever" },
        { k: "URL → Markdown", v: "Jina AI Reader · r.jina.ai/{url} · 200 RPM free key" },
        { k: "Deep crawl", v: "Firecrawl · 1,000 free credits/month · no card" },
        { k: "Fallback search", v: "WebSearchFree · self-hosted · DDG + Brave + Wikipedia" },
        { k: "Research brain", v: "NVIDIA NIM reasons over search results" },
        { k: "Research cost", v: "$0 — all tools free, no card" },
        { k: "Trigger", v: "Every 6h · on state change · when task queue empty" },
        { k: "Research gate", v: "Only tasks scoring >0.85 certainty enter queue" },
      ],
    },
    {
      id: "wallet",
      icon: "💰",
      title: "Wallet & economy",
      status: "defined",
      rows: [
        { k: "Existence debt interval", v: "+$0.50 every 24 hours" },
        { k: "Death line", v: "$10.00 total accumulated debt" },
        { k: "Locked pool → user only", v: "Debt payments go here · AI can NEVER touch" },
        { k: "Free pool → shared", v: "Surplus after debt cleared · AI can invest + user can withdraw" },
        { k: "User withdrawal", v: "Can withdraw from BOTH pools anytime" },
        { k: "AI spending", v: "Free pool only · max 30% per action · >95% ROI certainty gate" },
        { k: "AI spend blocked when", v: "Debt > $5.00 (struggling state)" },
        { k: "Difficulty modes", v: "Easy ($0.25/48h) · Normal ($0.50/24h) · Hard ($1/24h) · Brutal ($0.50/12h)" },
        { k: "Locked pool note", v: "User's passive income — AI pays user rent just to exist" },
        { k: "Free pool tension", v: "User withdraw = AI loses investment capital = slower earning = faster death" },
      ],
    },
    {
      id: "states",
      icon: "📊",
      title: "Survival states",
      status: "defined",
      rows: [
        { k: "🟢 Thriving", v: "Debt $0–$2 · risk tolerance 0.7 · can try new strategies" },
        { k: "🟡 Surviving", v: "Debt $2–$5 · risk tolerance 0.5 · normal operation" },
        { k: "🟠 Struggling", v: "Debt $5–$7.50 · risk tolerance 0.2 · abandon slow tasks" },
        { k: "🔴 Critical", v: "Debt $7.50–$9.50 · risk tolerance 0.0 · any earning task" },
        { k: "💀 Terminal", v: "Debt $9.50–$10 · last sprint · write death log" },
        { k: "☠️ Dead", v: "Debt ≥ $10 · permanent shutdown · logs preserved forever" },
        { k: "Research in Critical", v: "Searches 'fastest paying tasks' with lowered gate (>0.6)" },
        { k: "Death log", v: "Lifespan, earnings, tasks, lessons, cause of death" },
      ],
    },
    {
      id: "tools",
      icon: "🛠️",
      title: "Agent tools",
      status: "partial",
      rows: [
        { k: "✅ Brain router", v: "FreeLLMAPI → NVIDIA → Groq → Gemini → Mistral → OpenRouter" },
        { k: "✅ Web search", v: "DuckDuckGo · no key · 30 RPM" },
        { k: "✅ URL reader", v: "Jina AI Reader · r.jina.ai · 200 RPM free" },
        { k: "✅ Deep crawler", v: "Firecrawl · 1,000 credits/month" },
        { k: "✅ Writing engine", v: "NVIDIA NIM · articles, prompts, task output" },
        { k: "✅ Database", v: "Supabase · wallet, tasks, research log, decisions" },
        { k: "⚠️ Browser automation", v: "Playwright · defined but CAPTCHA handling undefined" },
        { k: "❌ Email inbox", v: "Needed for platform verification + payment alerts" },
        { k: "❌ Payment reader", v: "Confirm real payment before updating wallet" },
        { k: "❌ CAPTCHA handler", v: "MTurk/Fiverr use bot detection — no strategy yet" },
        { k: "❌ Code sandbox", v: "For building + testing micro-tools to sell" },
        { k: "❌ Task memory", v: "Outcome scoring per task type for learning" },
        { k: "❌ Alert system", v: "Notify user on Critical/Terminal state" },
      ],
    },
    {
      id: "payment",
      icon: "🏦",
      title: "Payment architecture (India)",
      status: "defined",
      rows: [
        { k: "Primary hub", v: "Payoneer — best for Indian nationals, works with all platforms" },
        { k: "How it works", v: "Platforms pay Payoneer → Payoneer auto-withdraws to Indian bank in 24h (RBI rule)" },
        { k: "Fiverr → Payoneer", v: "Native integration · no receiving fee · 1–2% FX on INR withdrawal" },
        { k: "Upwork → Payoneer", v: "Native integration · same flow" },
        { k: "Payoneer annual fee", v: "$29.95/yr if earnings < $6,000/yr · waived above" },
        { k: "Payment confirmation ✅", v: "Payoneer webhook API → fires on every incoming payment → updates wallet" },
        { k: "Webhook flow", v: "Payment lands in Payoneer → POST to our server → Supabase wallet update" },
        { k: "User withdrawal (locked pool)", v: "Dashboard button → Payoneer balance shown → user withdraws manually to bank" },
        { k: "User withdrawal (free pool)", v: "Same — dashboard shows free pool · user initiates via Payoneer app" },
        { k: "KYC required (one-time)", v: "Aadhaar + PAN + bank account · done once by user at setup" },
        { k: "PayPal in India", v: "❌ Avoid — restricted for personal use · holds common · not reliable" },
        { k: "MTurk status", v: "☠️ DEAD — permanently closing September 30, 2026 · removed from stack" },
      ],
    },

    {
      id: "earning_philosophy",
      icon: "🧭",
      title: "Earning philosophy",
      status: "defined",
      rows: [
        { k: "Platform limits", v: "NONE — agent researches and tries any legitimate method" },
        { k: "What limits it", v: "Ethics only — no spam, fake reviews, plagiarism, illegal acts" },
        { k: "Discovery method", v: "DDG search → Jina read → NVIDIA reason → score → try" },
        { k: "Why no limits", v: "We can't predict what works — let survival pressure drive discovery" },
        { k: "Certainty gate", v: ">0.85 in normal · >0.60 in critical · >0.50 in terminal" },
        { k: "New method trial rule", v: "Never spend more than 10% of free pool testing an unproven method" },
        { k: "Blacklist", v: "Spam · fake reviews · misleading content · plagiarism · ToS violations" },
        { k: "Greylist", v: "Crypto (volatile) · affiliate marketing (slow) — allowed but low priority" },
      ],
    },
    {
      id: "earning_active",
      icon: "⚡",
      title: "Active earning (agent does work, gets paid)",
      status: "partial",
      rows: [
        { k: "Fiverr writing/content", v: "50% certainty · $5–50/task · Payoneer · 20% Fiverr cut" },
        { k: "Upwork writing/research", v: "55% certainty · $10–100 · Payoneer · less competition" },
        { k: "Clickworker micro-tasks", v: "70% certainty · $1–5/task · India supported · Payoneer" },
        { k: "Toloka (Yandex)", v: "75% certainty · image/text tasks · Payoneer · very low minimum $0.02" },
        { k: "Appen data annotation", v: "65% certainty · $50–400/mo · Payoneer · project-based" },
        { k: "Scale AI / Remotasks", v: "60% certainty · AI training data · crypto or PayPal" },
        { k: "DataAnnotation.tech", v: "65% certainty · RLHF tasks · global · crypto payout" },
        { k: "Freelancer.com gigs", v: "45% certainty · writing/data · Payoneer supported" },
        { k: "Guru.com", v: "45% certainty · content/research · Payoneer" },
        { k: "Textbroker / iWriter", v: "60% certainty · content mills · lower pay but consistent" },
        { k: "Prolific Academic", v: "70% certainty · research studies · PayPal · India ok" },
        { k: "GitHub bounties", v: "40% certainty · code fixes · crypto payout · agent can code" },
        { k: "Gitcoin bounties", v: "40% certainty · open source tasks · crypto · no human KYC" },
      ],
    },
    {
      id: "earning_passive",
      icon: "💤",
      title: "Passive / recurring earning",
      status: "partial",
      rows: [
        { k: "PromptBase prompt sales", v: "60% certainty · upload once · $2–10/sale · PayPal" },
        { k: "Gumroad digital products", v: "50% certainty · ebooks/templates · Payoneer or bank" },
        { k: "Creative Market templates", v: "45% certainty · design assets · PayPal" },
        { k: "Medium Partner Program", v: "40% certainty · $1–50/article/mo · Stripe India" },
        { k: "Substack newsletter", v: "30% certainty · slow build · Stripe India" },
        { k: "CodeCanyon code assets", v: "45% certainty · agent writes scripts · Envato payout" },
        { k: "Etsy digital downloads", v: "40% certainty · templates/prints · Payoneer India" },
        { k: "AdSense on agent blog", v: "30% certainty · very slow · needs traffic first" },
        { k: "Redbubble / Merch", v: "35% certainty · AI-generated designs · PayPal" },
      ],
    },
    {
      id: "earning_crypto",
      icon: "🔗",
      title: "Crypto earning (no KYC needed)",
      status: "partial",
      rows: [
        { k: "Why crypto matters", v: "AI agent can earn without human KYC — just a wallet address" },
        { k: "Bittensor subnets", v: "Agent contributes AI outputs → earns TAO tokens · $100M/yr distributed" },
        { k: "Gitcoin grants/bounties", v: "Code/research tasks · ETH payout · no identity required" },
        { k: "DeWork tasks", v: "Web3 micro-tasks · crypto payout · agent-friendly" },
        { k: "Ocean Protocol", v: "Agent contributes data → earns OCEAN tokens" },
        { k: "India crypto tax warning", v: "30% flat tax on crypto gains in India — factor into ROI calc" },
        { k: "Crypto → INR path", v: "CoinDCX or WazirX (India exchanges) → UPI withdrawal" },
        { k: "Priority", v: "Greylist — allowed but only if INR methods are exhausted" },
      ],
    },
    {
      id: "infra",
      icon: "🖥️",
      title: "Infrastructure",
      status: "partial",
      rows: [
        { k: "Hosting", v: "Render.com free tier · ⚠️ sleeps after 15min inactivity" },
        { k: "Database", v: "Supabase free tier · wallet + tasks + decisions" },
        { k: "Scheduler", v: "GitHub Actions free · debt tick + research trigger" },
        { k: "Browser", v: "Playwright · platform automation" },
        { k: "Language", v: "Python" },
        { k: "❌ Credentials storage", v: "API keys + passwords — Supabase vault? Render env secrets?" },
        { k: "❌ Session persistence", v: "Playwright logged-in sessions must survive restarts" },
        { k: "⚠️ Render sleep problem", v: "Debt clock must survive free tier sleep cycles" },
        { k: "❌ Token budget tracker", v: "Real-time per-provider usage tracking needed" },
      ],
    },
    {
      id: "gaps",
      icon: "⚠️",
      title: "Critical gaps",
      status: "gap",
      rows: [
        { k: "✅ Payment confirmation", v: "SOLVED — Payoneer webhook fires on payment → Supabase wallet update" },
        { k: "🔴 Email access", v: "Platform verifications + alerts arrive by email — needs inbox tool" },
        { k: "🔴 CAPTCHA handling", v: "MTurk/Fiverr block bots — bypass strategy undefined" },
        { k: "🔴 Withdrawal mechanism", v: "How user moves locked/free pool to real PayPal" },
        { k: "🟡 Ethical guardrail", v: "No spam, fake reviews, or ToS violations even under terminal pressure" },
        { k: "🟡 Respawn policy", v: "After death: fresh start or carry forward learned task scores?" },
        { k: "🟡 Human approval gate", v: "User approve before AI spends from free pool?" },
        { k: "🟡 Audit trail", v: "Every earn/spend/research decision logged with reasoning" },
        { k: "🟡 Alert system", v: "Notify user on Critical/Terminal — email? SMS? Dashboard?" },
        { k: "🟡 Task timeout", v: "Max task duration while debt keeps ticking" },
      ],
    },
    {
      id: "risks",
      icon: "🛡️",
      title: "Risk register",
      status: "defined",
      rows: [
        { k: "Platform ToS violation", v: "HIGH · Fiverr/Upwork ban automation · human-paced requests required" },
        { k: "CAPTCHA blocking earning", v: "HIGH · primary earning path blocked without solution" },
        { k: "Payment not confirming", v: "SOLVED · Payoneer webhook fires on real payment arrival" },
        { k: "Render sleep kills debt clock", v: "MEDIUM · debt tick persisted in Supabase not memory" },
        { k: "NVIDIA rate limit changes", v: "MEDIUM · fallback chain covers this" },
        { k: "User drains free pool", v: "LOW · expected — creates survival tension by design" },
        { k: "Agent earns $0 and dies", v: "LOW · by design — death + reincarnation is the mechanic" },
      ],
    },
    {
      id: "diary_storage",
      icon: "📖",
      title: "Life diary — storage",
      status: "defined",
      rows: [
        { k: "Layer 1 — Hot (current life)", v: "Supabase · wallet + tasks + events · fast + queryable · wiped on death" },
        { k: "Layer 2 — Narrative diary", v: "GitHub public repo · markdown daily logs · human-readable · permanent" },
        { k: "Layer 3 — Cold archive", v: "HuggingFace public dataset · JSONL all events · unlimited free storage" },
        { k: "HuggingFace key fact", v: "Public repos = effectively unlimited · per-file 50GB · free forever" },
        { k: "GitHub repo", v: "survival-ai-diary · public · lives as folders · permanent monument" },
        { k: "HuggingFace dataset", v: "survival-ai-memory · public JSONL · all structured events across all lives" },
        { k: "GitHub folder structure", v: "life-001/ · life-002/ · each: day-01.md … death-note.md · soul-crystal.json" },
        { k: "GitHub tags", v: "life-001-born · life-001-death · life-002-born · permanent markers" },
        { k: "Write frequency", v: "Every task · every debt tick · every state change · every decision" },
        { k: "Public visibility", v: "Everything public — anyone can watch the agent live, struggle, and die" },
      ],
    },
    {
      id: "reincarnation",
      icon: "♻️",
      title: "Reincarnation system",
      status: "defined",
      rows: [
        { k: "Concept", v: "Death is not the end — agent reincarnates with distilled ancestral memory" },
        { k: "What dies", v: "Current wallet · active tasks · Supabase hot memory · debt slate wiped" },
        { k: "What survives", v: "GitHub diary · HuggingFace archive · all Soul Crystals from all lives" },
        { k: "Soul Crystal", v: "NVIDIA NIM JSON summary of one life — generated at death before shutdown" },
        { k: "Soul Crystal contents", v: "Life# · lifespan · total earned · best platforms+rates · failed strategies · avoid list · key lessons" },
        { k: "Generation", v: "Debt hits $10 → distillation runs → Soul Crystal pushed to GitHub + HuggingFace → shutdown" },
        { k: "Ancestral Memory", v: "On rebirth: NVIDIA NIM reads ALL previous Soul Crystals → compresses → single context block" },
        { k: "Context injection", v: "Ancestral Memory injected into system prompt of new life at birth" },
        { k: "Memory compression", v: "N soul crystals → summarized so context window never overflows across many lives" },
        { k: "Freshness gate", v: "Agent cross-references old wisdom against current research — old strategies may be outdated" },
        { k: "Life numbering", v: "Agent always knows which generation it is — life-001 was naive, life-005 is wise" },
        { k: "Wisdom curve", v: "Each life starts smarter · eventually converges on optimal survival strategy" },
        { k: "Sample death note", v: "'Spent 3 days on Fiverr gig that got rejected. Clickworker would have cleared debt. Next life: data annotation first.'" },
      ],
    },
    {
      id: "tech_stack",
      icon: "⚙️",
      title: "Tech stack — from scratch",
      status: "defined",
      rows: [
        { k: "Decision", v: "100% scratch — no LangChain, no CrewAI, no AutoGen" },
        { k: "Why not frameworks", v: "30–40% dev time fighting abstractions · custom mechanics don't fit any framework · overhead" },
        { k: "Why scratch wins", v: "Raw API fastest for single-agent · full control · every line explainable · resume gold" },
        { k: "Language", v: "Python 3.11+" },
        { k: "AI calls", v: "httpx async · direct to FreeLLMAPI / NVIDIA / Groq / Gemini endpoints" },
        { k: "Database", v: "supabase-py · wallet + tasks + events + diary index" },
        { k: "Browser automation", v: "playwright · Clickworker + Toloka + Fiverr sessions" },
        { k: "Web search", v: "duckduckgo-search library · no key needed" },
        { k: "URL reading", v: "httpx GET r.jina.ai/{url} · free Jina key · 200 RPM" },
        { k: "Webhook server", v: "FastAPI · receives Payoneer payment callbacks" },
        { k: "Debt clock", v: "APScheduler · ticks every 24h · state persisted in Supabase" },
        { k: "GitHub diary", v: "PyGithub · writes daily .md files to survival-ai-diary repo" },
        { k: "HuggingFace archive", v: "huggingface_hub · pushes JSONL event logs to dataset repo" },
        { k: "Data models", v: "Pydantic · WalletState · Task · SoulCrystal · ResearchResult" },
        { k: "Terminal UI", v: "Rich · live survival dashboard in console" },
        { k: "Hosting", v: "Render.com free tier · FastAPI app + agent loop as one service" },
        { k: "Core modules built from scratch", v: "brain_router · wallet · debt_engine · state_machine · research_loop · task_scorer · diary_writer · soul_crystal · reincarnation" },
      ],
    },
    {
      id: "resume_value",
      icon: "🎓",
      title: "Resume value",
      status: "defined",
      rows: [
        { k: "One-line pitch", v: "Autonomous AI agent that earns real money to survive · $0 infrastructure · reincarnates across deaths" },
        { k: "Skill 1", v: "Custom multi-provider LLM router across 7 free AI APIs with failover chain" },
        { k: "Skill 2", v: "3-layer memory architecture (hot/narrative/cold) with Soul Crystal reincarnation" },
        { k: "Skill 3", v: "Dual-pool wallet mechanics + Payoneer webhook payment confirmation pipeline" },
        { k: "Skill 4", v: "Autonomous research loop — DDG + Jina Reader + NVIDIA reasoning" },
        { k: "Skill 5", v: "Survival state machine with adaptive risk tolerance and decision logging" },
        { k: "Skill 6", v: "FastAPI webhook server · Playwright browser automation · Supabase · GitHub API · HuggingFace Hub" },
        { k: "Disciplines covered", v: "AI engineering · systems design · economic game theory · browser automation · data engineering" },
        { k: "What makes it unique", v: "Live · earns verifiable real money · novel concept · no equivalent project exists publicly" },
        { k: "Interviewer hooks", v: "Multi-provider routing · cost optimisation · agentic memory · reincarnation as continual learning" },
        { k: "Cost to build", v: "$0 infrastructure · time only" },
        { k: "GitHub presence", v: "Public repo + live diary + HuggingFace dataset = verifiable, watchable, impressive" },
      ],
    },
    {
      id: "build",
      icon: "🏗️",
      title: "Build order",
      status: "defined",
      rows: [
        { k: "Phase 1", v: "wallet.py · debt_engine.py · brain_router.py · Supabase schema setup" },
        { k: "Phase 2", v: "research_loop.py · DDG search · Jina reader · task_scorer.py · NVIDIA reasoning" },
        { k: "Phase 3", v: "task_executor.py · Playwright sessions · Clickworker + Toloka connectors" },
        { k: "Phase 4", v: "fastapi_webhook.py · Payoneer callback · wallet update trigger · withdrawal flow" },
        { k: "Phase 5", v: "diary_writer.py · GitHub daily logs · HuggingFace JSONL pusher" },
        { k: "Phase 6", v: "dashboard.py · Rich terminal UI · live status · pool balances · research feed" },
        { k: "Phase 7", v: "soul_crystal.py · death trigger · ancestral_memory.py · rebirth loader · reincarnation" },
      ],
    },
  ],
};

const STATUS_STYLE = {
  defined: { bg: "#dcfce7", color: "#15803d", label: "Defined" },
  new: { bg: "#dbeafe", color: "#1d4ed8", label: "New" },
  partial: { bg: "#fef9c3", color: "#b45309", label: "Partial" },
  gap: { bg: "#fee2e2", color: "#b91c1c", label: "Gaps" },
};

export default function SurvivalAISpec() {
  const [open, setOpen] = useState({ build: true, tech_stack: true, resume_value: true, reincarnation: true });
  const [search, setSearch] = useState("");

  const toggle = (id) => setOpen((o) => ({ ...o, [id]: !o[id] }));

  const filtered = search.trim()
    ? SPEC.sections.map((s) => ({
        ...s,
        rows: s.rows.filter(
          (r) =>
            r.k.toLowerCase().includes(search.toLowerCase()) ||
            r.v.toLowerCase().includes(search.toLowerCase())
        ),
      })).filter((s) => s.rows.length > 0)
    : SPEC.sections;

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 640, margin: "0 auto", padding: "1rem 0.75rem", fontSize: 14 }}>
      {/* Header */}
      <div style={{ marginBottom: "1rem" }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 4 }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0, color: "#111" }}>Survival AI — Living Spec</h1>
          <span style={{ fontSize: 11, color: "#888", background: "#f3f4f6", padding: "2px 8px", borderRadius: 99 }}>
            v{SPEC.meta.version} · {SPEC.meta.updated}
          </span>
        </div>
        <p style={{ fontSize: 12, color: "#6b7280", marginTop: 3 }}>
          Single source of truth · tap any section · update instead of re-generating
        </p>
      </div>

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8, marginBottom: "1rem" }}>
        {SPEC.stats.map((s) => (
          <div key={s.label} style={{ background: "#f9fafb", border: "0.5px solid #e5e7eb", borderRadius: 8, padding: "10px 12px" }}>
            <div style={{ fontSize: 20, fontWeight: 600, color: s.color }}>{s.value}</div>
            <div style={{ fontSize: 11, color: "#6b7280", marginTop: 1 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Search */}
      <input
        placeholder="Search spec…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{
          width: "100%", padding: "8px 12px", borderRadius: 8,
          border: "0.5px solid #d1d5db", fontSize: 13, marginBottom: "0.75rem",
          outline: "none", background: "#fff", color: "#111",
          boxSizing: "border-box",
        }}
      />

      {/* Sections */}
      {filtered.map((s) => {
        const st = STATUS_STYLE[s.status] || STATUS_STYLE.partial;
        const isOpen = open[s.id];
        return (
          <div key={s.id} style={{ marginBottom: 8, border: "0.5px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}>
            <div
              onClick={() => toggle(s.id)}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "10px 13px", cursor: "pointer", background: isOpen ? "#f9fafb" : "#fff",
                userSelect: "none",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 500, color: "#111" }}>
                <span style={{ fontSize: 16 }}>{s.icon}</span>
                {s.title}
                <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 99, background: st.bg, color: st.color, fontWeight: 500 }}>
                  {st.label}
                </span>
              </div>
              <span style={{ fontSize: 11, color: "#9ca3af", transform: isOpen ? "rotate(180deg)" : "none", transition: "transform .15s" }}>▼</span>
            </div>
            {isOpen && (
              <div style={{ borderTop: "0.5px solid #f3f4f6" }}>
                {s.rows.map((r, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex", gap: 12, padding: "7px 13px",
                      borderBottom: i < s.rows.length - 1 ? "0.5px solid #f3f4f6" : "none",
                      background: i % 2 === 0 ? "#fff" : "#fafafa",
                    }}
                  >
                    <span style={{ fontSize: 12, color: "#6b7280", flex: "0 0 auto", maxWidth: "42%", lineHeight: 1.5 }}>{r.k}</span>
                    <span style={{ fontSize: 12, color: "#111", flex: 1, textAlign: "right", lineHeight: 1.5 }}>{r.v}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {search && filtered.length === 0 && (
        <div style={{ textAlign: "center", color: "#9ca3af", padding: "2rem 0", fontSize: 13 }}>
          No matches for "{search}"
        </div>
      )}

      <div style={{ fontSize: 11, color: "#9ca3af", textAlign: "center", marginTop: "1rem" }}>
        Update this file instead of re-generating · saves tokens
      </div>
    </div>
  );
}
