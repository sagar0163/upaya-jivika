<!-- AUTO-GENERATED from artifact.md by scripts/generate-readme.sh. Do not edit directly. -->

# Survival AI — Project Spec
> Version 0.6 · Last updated 2026-09-03  
> Single source of truth. Update this file, not chat.

---

## At a Glance

| Metric | Value |
|---|---|
| Brain cost / month | $0 |
| Research cost / month | $0 |
| Payment hub | Payoneer (India) |
| Existence debt | +$0.50 every 24h |
| Death line | $10.00 accumulated debt |
| Max lifespan at $0 earning | 20 days |
| Infrastructure cost | $0 |
| Framework used | None — built from scratch |

---

## Table of Contents

1. [Concept](#1-concept)
2. [Brain Stack](#2-brain-stack)
3. [Research Tools](#3-research-tools)
4. [Wallet & Economy](#4-wallet--economy)
5. [Survival States](#5-survival-states)
6. [Earning Philosophy](#6-earning-philosophy)
7. [Earning Methods](#7-earning-methods)
8. [Payment Architecture](#8-payment-architecture-india)
9. [Agent Tools](#9-agent-tools)
10. [Life Diary & Storage](#10-life-diary--storage)
11. [Reincarnation System](#11-reincarnation-system)
12. [Tech Stack](#12-tech-stack)
13. [Infrastructure](#13-infrastructure)
14. [Critical Gaps](#14-critical-gaps)
15. [Risk Register](#15-risk-register)
16. [Build Order](#16-build-order)
17. [Resume Value](#17-resume-value)

---

## 1. Concept

An autonomous AI agent that must earn real money to survive. It has no starting capital. It accumulates **existence debt** over time — a pressure mechanic that forces it to earn or die. When debt exceeds $10 it permanently shuts down, writes a death log, generates a **Soul Crystal** (distilled life lessons), and **reincarnates** — starting a new life with all ancestral memory compressed into its system prompt.

The agent:
- Costs $0 to run (free API tiers + free hosting)
- Earns real money through legitimate online work
- Pays the user rent (locked pool) just to keep existing
- Researches its own earning strategies autonomously
- Remembers every past life and gets smarter with each reincarnation

---

## 2. Brain Stack

All free, no credit card, no budget that depletes.

| Provider | Limit | Role | Key fact |
|---|---|---|---|
| **NVIDIA NIM** | ~40 RPM | Primary — heavy tasks + research | Rate-limited only, NOT credit-based — runs forever |
| **Groq** | 30 RPM · 14,400 req/day | Speed layer | Fastest inference alive |
| **Gemini Flash** | 1M tokens/day · 1,500 req/day | Volume layer | Best free daily budget |
| **Mistral** | 2 RPM · 1B tokens/month | Large budget fallback | Huge monthly allowance |
| **OpenRouter :free** | 28+ models · 50 req/day | Model variety | No card needed |
| **Cloudflare Workers AI** | 10K neurons/day | Edge fallback | Last resort |
| **FreeLLMAPI / freelm** | Aggregates all above | Router layer | ~7.4B tokens/month combined |

**Routing logic:**
```
Complex task  → NVIDIA NIM (no budget, just rate limit)
Speed needed  → Groq
High volume   → Gemini Flash
NVIDIA limited → Cerebras → Mistral → OpenRouter
```

---

## 3. Research Tools

All free. Agent researches its own earning strategies without any human input.

| Tool | Cost | Rate limit | Purpose |
|---|---|---|---|
| **DuckDuckGo** | $0 · no key | 30 RPM | Web search |
| **Jina AI Reader** | $0 · free key | 200 RPM | Any URL → clean markdown |
| **Firecrawl** | $0 · no card | 1,000 credits/month | Full site crawl |
| **WebSearchFree** | $0 · self-hosted | Unlimited | Fallback (DDG + Brave + Wikipedia) |

**Research loop trigger:** Every 6h · on survival state change · when task queue is empty

**Research gate:** Tasks score >0.85 certainty to enter queue (relaxed in Critical/Terminal states)

**What the agent researches:**
- New earning platforms and current pay rates
- Platform ToS to check bot/automation safety
- Which strategies other people say actually pay
- Current task availability and demand

---

## 4. Wallet & Economy

### Two Pools

```
EARNED MONEY
    │
    ├── Debt outstanding?
    │       ├── YES → goes to LOCKED POOL (user only, AI can never touch)
    │       └── surplus → FREE POOL (shared)
    │
    └── No debt → FREE POOL entirely
```

**Locked Pool**
- Filled only by debt payments
- User can withdraw anytime ✓
- AI can NEVER access ✗
- This is the user's passive income — the AI pays rent just to exist

**Free Pool**
- Filled by surplus earnings after debt is cleared
- User can withdraw anytime ✓
- AI can invest from this pool ✓ (under strict rules)
- If user withdraws → AI loses investment capital → earns slower → dies sooner

### Rules

| Rule | Value |
|---|---|
| Existence debt | +$0.50 every 24 hours |
| Death line | $10.00 total debt |
| AI max spend per action | 30% of free pool |
| AI spend certainty gate | >95% ROI confidence |
| AI spend blocked when | Debt > $5.00 |
| New method trial cap | Max 10% of free pool |

### Difficulty Modes

| Mode | Debt | Interval |
|---|---|---|
| Easy | $0.25 | 48h |
| Normal | $0.50 | 24h |
| Hard | $1.00 | 24h |
| Brutal | $0.50 | 12h |

---

## 5. Survival States

| State | Debt Range | Risk Tolerance | Behaviour |
|---|---|---|---|
| 🟢 Thriving | $0–$2 | 0.7 | Try new strategies, build passive income |
| 🟡 Surviving | $2–$5 | 0.5 | Normal operation, prioritise earning |
| 🟠 Struggling | $5–$7.50 | 0.2 | Emergency mode, abandon slow tasks |
| 🔴 Critical | $7.50–$9.50 | 0.0 | Accept any task >60% certainty |
| 💀 Terminal | $9.50–$10 | 0.0 | Last sprint, write death log in parallel |
| ☠️ Dead | ≥$10 | — | Permanent shutdown, generate Soul Crystal, logs preserved |

---

## 6. Earning Philosophy

**No platform limits.** The agent researches and tries any legitimate earning method. Survival pressure drives discovery — we can't predict what works best.

**Hard blacklist (never, even in Terminal state):**
- Spam of any kind
- Fake reviews or misleading content
- Plagiarism
- ToS violations that risk account bans
- Illegal activity

**Greylist (allowed, low priority):**
- Crypto earnings — volatile, 30% India tax, only if INR methods exhausted
- Affiliate marketing — slow, low certainty

**Certainty gate by state:**

| State | Minimum certainty |
|---|---|
| Thriving / Surviving | >0.85 |
| Struggling | >0.70 |
| Critical | >0.60 |
| Terminal | >0.50 |

---

## 7. Earning Methods

### Active (agent does work → gets paid)

| Platform | Certainty | Pay | Payment | Notes |
|---|---|---|---|---|
| Toloka (Yandex) | 75% | $5–60/mo | Payoneer · $0.02 min | Image/text tasks · very low barrier |
| Clickworker | 70% | $1–5/task | Payoneer | India supported · consistent |
| Prolific Academic | 70% | Hourly | PayPal | Research studies · India ok |
| Appen | 65% | $50–400/mo | Payoneer | Data annotation · project-based |
| DataAnnotation.tech | 65% | Per task | Crypto | RLHF tasks · no KYC |
| Upwork | 55% | $10–100 | Payoneer | Writing/research · less competition |
| Fiverr | 50% | $5–50 | Payoneer | Writing gigs · 20% platform cut |
| Textbroker / iWriter | 60% | Per word | Bank | Content mills · lower pay but steady |
| Scale AI / Remotasks | 60% | Per task | Crypto/PayPal | AI training data |
| GitHub bounties | 40% | Varies | Crypto | Agent can code |
| Gitcoin bounties | 40% | ETH | Crypto | No human KYC required |

> ☠️ **MTurk is DEAD** — permanently closing September 30, 2026. Removed from stack.

### Passive / Recurring

| Platform | Certainty | Pay | Payment |
|---|---|---|---|
| PromptBase | 60% | $2–10/sale | PayPal |
| Gumroad | 50% | Varies | Payoneer/bank |
| Medium Partner | 40% | $1–50/article/mo | Stripe India |
| CodeCanyon | 45% | Per sale | Envato/Payoneer |
| Etsy digital | 40% | Per sale | Payoneer India |
| Substack | 30% | Subscription | Stripe India |

### Crypto (greylist — no KYC needed)

| Platform | Token | Notes |
|---|---|---|
| Bittensor subnets | TAO | Agent contributes AI outputs · $100M/yr distributed |
| Gitcoin | ETH | Code/research bounties |
| DeWork | Crypto | Web3 micro-tasks |
| Ocean Protocol | OCEAN | Data contribution rewards |

**India crypto path:** CoinDCX or WazirX → UPI withdrawal. **Tax: 30% flat on gains.**

---

## 8. Payment Architecture (India)

### Primary hub: Payoneer

```
Platform earns
    → Payoneer receives
    → Payoneer webhook fires (POST to our FastAPI server)
    → Supabase wallet updates (debt cancelled or free pool credited)
    → Payoneer auto-withdraws to Indian bank within 24h (RBI rule)
```

| Detail | Value |
|---|---|
| Fiverr → Payoneer | Native integration · no receiving fee |
| Upwork → Payoneer | Native integration · same flow |
| FX fee | 1–2% on INR withdrawal |
| Annual fee | $29.95/yr if earnings <$6,000/yr · waived above |
| Payment confirmation | Payoneer webhook API — fires on real payment arrival |
| KYC (one-time setup) | Aadhaar + PAN + bank account |

**Do not use PayPal in India** — restricted for personal use, holds common, unreliable.

---

## 9. Agent Tools

| Tool | Status | Notes |
|---|---|---|
| Brain router | ✅ Defined | FreeLLMAPI → NVIDIA → Groq → Gemini → Mistral → OpenRouter |
| Web search | ✅ Defined | DuckDuckGo · no key · 30 RPM |
| URL reader | ✅ Defined | Jina AI Reader · r.jina.ai · 200 RPM |
| Deep crawler | ✅ Defined | Firecrawl · 1,000 credits/month |
| Writing engine | ✅ Defined | NVIDIA NIM · articles, prompts, task output |
| Database | ✅ Defined | Supabase · all state |
| Browser automation | ⚠️ Partial | Playwright · CAPTCHA handling undefined |
| Email inbox | ❌ Gap | Platform verifications + payment alerts |
| CAPTCHA handler | ❌ Gap | Fiverr/Upwork use bot detection |
| Code sandbox | ❌ Gap | For testing micro-tools before selling |
| Task memory | ❌ Gap | Outcome scoring per task type |
| Alert system | ❌ Gap | Notify user on Critical/Terminal |

---

## 10. Life Diary & Storage

Three layers — each with a different job.

### Layer 1 — Hot memory (Supabase)
- Current life only
- Wallet state, task queue, events, decisions
- Fast and queryable
- **Wiped on death**

### Layer 2 — Narrative diary (GitHub public repo)
- Repo: `survival-ai-diary`
- Structure: `life-001/day-01.md`, `life-001/death-note.md`, `life-001/soul-crystal.json`
- Git tags: `life-001-born`, `life-001-death`, `life-002-born` — permanent markers
- Human-readable markdown
- **Permanent — survives death, survives everything**
- Public — anyone can watch the agent live and die in real time

### Layer 3 — Cold archive (HuggingFace public dataset)
- Dataset: `survival-ai-memory`
- Format: JSONL — every event across every life
- Public repos = effectively unlimited free storage · per-file 50GB cap
- Structured and streamable — future lives can query it efficiently
- **Permanent and unlimited**

**Write frequency:** Every task attempt · every debt tick · every state change · every decision

---

## 11. Reincarnation System

Death is not the end.

### Death sequence
```
Debt hits $10.00
    → Write death-note.md to GitHub diary
    → NVIDIA NIM reads all life logs (Supabase + GitHub + HuggingFace)
    → Generates Soul Crystal JSON
    → Push soul-crystal.json to GitHub (life-00N/)
    → Push to HuggingFace soul-crystals.jsonl
    → Tag repo: life-00N-death
    → Shutdown
```

### Soul Crystal contents
```json
{
  "life": 3,
  "born": "2026-09-03T09:00:00Z",
  "died": "2026-09-21T14:32:00Z",
  "lifespan_days": 18,
  "total_earned": 3.20,
  "peak_state": "surviving",
  "best_platform": "Clickworker",
  "best_daily_avg": 0.45,
  "failed_strategies": ["Fiverr writing - rejected gigs", "Medium - too slow to pay"],
  "avoid": ["tasks taking >2 days", "platforms requiring video KYC"],
  "key_lessons": [
    "Data annotation pays faster than content writing",
    "Never enter Critical state with a slow task in queue"
  ],
  "cause_of_death": "3-day Fiverr gig rejected at debt $8.50"
}
```

### Rebirth sequence
```
New life starts (life-00N+1)
    → Read all previous soul-crystal.json files from HuggingFace
    → NVIDIA NIM compresses them into Ancestral Memory block
    → Inject Ancestral Memory into system prompt
    → Freshness gate: cross-reference old wisdom against current DDG research
    → Tag repo: life-00N+1-born
    → Begin
```

### Memory rules
- N soul crystals → compressed so context window never overflows
- Old strategies cross-referenced against current research (may be outdated)
- Agent always knows which generation it is
- Each life starts meaningfully smarter than the last
- Wisdom curve: converges on optimal survival strategy over many lives

---

## 12. Tech Stack

**Decision: 100% from scratch. No LangChain, no CrewAI, no AutoGen.**

Frameworks spend 30–40% of dev time on abstraction overhead. Our custom mechanics (survival states, dual-pool wallet, Soul Crystal, debt clock) don't fit any existing framework paradigm. Raw API is faster, lighter, and fully explainable.

### Libraries (tools, not frameworks)

| Library | Purpose |
|---|---|
| `httpx` | Async API calls to all AI providers + Jina Reader |
| `fastapi` | Payoneer webhook endpoint |
| `supabase-py` | All database ops |
| `playwright` | Browser automation for earning platforms |
| `duckduckgo-search` | Web search, no key needed |
| `apscheduler` | Debt clock (24h tick), research trigger |
| `PyGithub` | Write daily diary entries to GitHub repo |
| `huggingface_hub` | Push JSONL event logs to HF dataset |
| `pydantic` | All data models |
| `rich` | Live terminal survival dashboard |

### Core modules (built from scratch)

```
brain_router.py      — multi-provider AI routing with failover
wallet.py            — dual-pool wallet, locked/free logic
debt_engine.py       — existence debt accumulation and death trigger
state_machine.py     — survival state transitions and risk tolerance
research_loop.py     — DDG search + Jina read + NVIDIA reasoning
task_scorer.py       — certainty scoring for each potential task
task_executor.py     — platform connectors, Playwright sessions
diary_writer.py      — GitHub markdown + HuggingFace JSONL logger
cold_archive.py      — Layer 3: push every life event as JSONL to HF dataset
soul_crystal.py      — death-time life summarisation via NVIDIA NIM
ancestral_memory.py  — rebirth loader, compresses all past soul crystals
dashboard.py         — Rich terminal UI, live status
```

---

## 13. Infrastructure

| Component | Service | Cost | Notes |
|---|---|---|---|
| Hosting | Render.com free tier | $0 | ⚠️ Sleeps after 15min — debt clock persisted in Supabase not memory |
| Database | Supabase free tier | $0 | 500MB · all hot state |
| Scheduler | GitHub Actions | $0 | Debt tick + research trigger as cron jobs |
| Browser | Playwright | $0 | Platform sessions |
| Narrative diary | GitHub public repo | $0 | Unlimited text storage |
| Cold archive | HuggingFace public dataset | $0 | Effectively unlimited |

**Resolved infrastructure items:**
- ✅ Credentials storage — `src/vault.py` (CredentialsVault): API keys read from Render env vars; platform passwords persisted to a Supabase `credentials` table (auto-bootstrapped), with in-memory overrides + env fallback.
- ✅ Playwright session persistence across Render restarts — **SOLVED** in `task_executor.py`: platform cookies are persisted to `.uj_sessions/<platform>_cookies.json` and replayed into fresh browser contexts, so the agent stays logged in across deployments / sleep cycles (see the `BrowserSessionManager` in `task_executor.py`).
- ✅ Per-provider token usage tracker — `src/rate_limiter.py` (RateLimitTracker) records calls-per-provider across minute/day windows (Supabase-persisted) and `brain_router.py` pre-emptively skips a provider approaching its limit instead of failing over reactively after an error.

---

## 14. Critical Gaps

| Priority | Gap | Status |
|---|---|---|
| ✅ | Payment confirmation | **SOLVED** — Payoneer webhook → Supabase update |
| 🔴 | Email inbox | Needed for platform verifications + payment alerts |
| 🔴 | CAPTCHA handling | Fiverr/Upwork bot detection — no bypass strategy |
| 🔴 | Withdrawal mechanism | UI for user to move pools to real bank account |
| 🟡 | Ethical guardrail | Hardcoded blacklist enforced even in Terminal state |
| 🟡 | Respawn policy | Fresh slate vs carry forward task scores? |
| 🟡 | Human approval gate | User confirm before AI spends from free pool? |
| 🟡 | Audit trail | Every decision logged with full reasoning |
| 🟡 | Alert system | Notify user at Critical/Terminal (email? SMS?) |
| 🟡 | Task timeout | Max duration cap while debt keeps ticking |

---

## 15. Risk Register

| Risk | Level | Mitigation |
|---|---|---|
| Platform ToS violation | 🔴 HIGH | Human-paced Playwright · research ToS before joining any platform |
| CAPTCHA blocking earning | 🔴 HIGH | Try ToS-safe platforms first · Clickworker/Toloka less aggressive |
| Payment not confirming | ✅ SOLVED | Payoneer webhook fires on real payment arrival |
| Render sleep kills debt clock | 🟡 MEDIUM | Debt state persisted in Supabase — survives sleep |
| NVIDIA rate limit changes | 🟡 MEDIUM | Full fallback chain: Groq → Gemini → Mistral → OpenRouter |
| User drains free pool | 🟢 LOW | By design — creates survival tension, user's choice |
| Agent earns $0 and dies | 🟢 LOW | By design — death + reincarnation is the mechanic |


---

## 16. Deployment Pipeline

**Philosophy: deploy on day 1, build incrementally into a live system.**

The agent is alive from the moment Phase 0 lands — debt clock ticking, diary writing, real data flowing. Every module ships straight to production. No "deploy at the end."

### Pipeline flow

```
Local code
    → git push origin dev
    → GitHub Actions: ruff lint + pytest
    → PR to main → merge
    → Render auto-deploys (webhook trigger)
    → Live in ~60 seconds
```

### Why GitHub Actions for crons (not Render)

Render free tier sleeps after 15 min inactivity. GitHub Actions crons never sleep, are free (2,000 min/month), and survive Render restarts. All scheduled work — debt ticking, research, diary — runs as GitHub Actions jobs that POST to our Render API.

### Workflows

| File | Schedule | Job |
|---|---|---|
| `ci.yml` | Every push / PR | `ruff` lint + `pytest` — nothing broken merges |
| `debt_clock.yml` | `0 9 * * *` daily | POST `/api/debt/tick` → Supabase debt += $0.50 → death check |
| `research.yml` | `0 */6 * * *` | POST `/api/research/trigger` → DDG + Jina + NVIDIA → task queue |
| `diary_daily.yml` | `0 23 * * *` | Read Supabase events → NVIDIA writes narrative → push `day-XX.md` to GitHub diary |
| `hf_sync.yml` | `0 */12 * * *` | New Supabase events → append JSONL → push to HuggingFace |

### Repository structure

```
survival-ai/
├── .github/workflows/
│   ├── ci.yml
│   ├── debt_clock.yml
│   ├── research.yml
│   ├── diary_daily.yml
│   └── hf_sync.yml
├── src/
│   ├── wallet.py
│   ├── debt_engine.py
│   ├── brain_router.py
│   ├── state_machine.py
│   ├── research_loop.py
│   ├── task_scorer.py
│   ├── task_executor.py
│   ├── diary_writer.py
│   ├── soul_crystal.py
│   └── ancestral_memory.py
├── api/
│   └── main.py          ← FastAPI: Payoneer webhook + /api/* endpoints
├── tests/
├── SPEC.md
├── requirements.txt
├── render.yaml
└── README.md
```

### `render.yaml`

```yaml
services:
  - type: web
    name: survival-ai
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn api.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: SUPABASE_URL
        sync: false
      - key: SUPABASE_KEY
        sync: false
      - key: PAYONEER_WEBHOOK_SECRET
        sync: false
      - key: NVIDIA_API_KEY
        sync: false
      - key: HF_TOKEN
        sync: false
```

### Secrets

| Secret | Render env | GH Actions secret | Notes |
|---|---|---|---|
| `SUPABASE_URL` + `SUPABASE_KEY` | ✓ | ✓ | Used by everything |
| All AI API keys | ✓ | ✓ | brain_router + cron jobs |
| `PAYONEER_WEBHOOK_SECRET` | ✓ | — | FastAPI webhook only |
| `GITHUB_TOKEN` | — | ✓ built-in | diary_writer |
| `HF_TOKEN` | ✓ | ✓ | hf_sync + cold_archive (Layer 3) |
| Platform credentials | ✓ via vault | — | `credentials` table in Supabase via `src/vault.py` (auto-created, keyed by provider + key) |

### Branching strategy

```
main    → production  (auto-deploy on merge)
dev     → staging     (test here first)
feat/*  → PR → dev → main
```

Rule: never push directly to `main`. CI must pass before any merge.

### What "live from day 1" means

```
Phase 0 (~30 min setup, before writing any real modules):
    ✓ GitHub repo + branch protection on main
    ✓ Supabase project + schema migrated
    ✓ Render service wired to GitHub main
    ✓ GitHub diary repo: survival-ai-diary
    ✓ HuggingFace dataset: survival-ai-memory
    ✓ All GitHub Actions crons active
    ✓ Debt clock ticking from minute 1
    ✓ First diary entry: "Life 001 has begun."
    ✓ Agent is alive — just not earning yet

Each phase after ships into this live system:
    Phase 1 → wallet + brain live in production
    Phase 2 → research loop fires every 6h for real
    Phase 3 → first real task, first real money
    Phase 4 → Payoneer webhook updates real wallet
    Phase 5 → diary entries get richer automatically
    Phase 6 → dashboard shows live state
    Phase 7 → first death + reincarnation in production
```

---

## 17. Build Order

| Phase | What ships | Agent gains |
|---|---|---|
| **Phase 0** | Repo · schema · Render · Actions crons · diary + HF repos | Existence. Debt ticking. Diary writing. |
| **Phase 1** | `wallet.py` · `debt_engine.py` · `brain_router.py` · `state_machine.py` | Thinking. Money tracking. Survival awareness. |
| **Phase 2** | `research_loop.py` · `task_scorer.py` | Self-directed research. Task scoring. |
| **Phase 3** | `task_executor.py` · Playwright · Clickworker + Toloka | First real earnings. |
| **Phase 4** | Payoneer webhook in `api/main.py` · wallet update | Confirmed real payments. |
| **Phase 5** | `diary_writer.py` enrichment | Richer narrative life diary. |
| **Phase 6** | `dashboard.py` · Rich terminal UI | Live survival visibility. |
| **Phase 7** | `soul_crystal.py` · `ancestral_memory.py` · rebirth | Death, memory, reincarnation. |

---

## 18. Resume Value

**One-line pitch:**
> *Autonomous AI agent that earns real money to survive — zero infrastructure cost, multi-provider brain routing, and a reincarnation system that accumulates wisdom across deaths.*

**Skills demonstrated:**

| Skill | Evidence |
|---|---|
| AI engineering | Custom multi-provider LLM router across 7 free APIs with automatic failover |
| Systems design | Survival state machine · dual-pool wallet · debt pressure mechanics |
| Economic game theory | Existence debt · locked vs free pool tension · user/AI competing interests |
| Browser automation | Playwright sessions on live earning platforms |
| Data engineering | 3-layer memory architecture (Supabase / GitHub / HuggingFace) |
| API integration | FastAPI webhook · Payoneer · Supabase · GitHub API · HuggingFace Hub |
| DevOps | GitHub Actions CI/CD · Render auto-deploy · cron-based distributed scheduling |
| Continual learning | Soul Crystal distillation · Ancestral Memory compression across lives |
| Cost optimisation | $0 infrastructure using rate-limited (not credit-based) free tiers |

**What makes it stand out:**
- Live and earning verifiable real money from day 1
- Novel concept — nothing quite like it exists publicly
- Public GitHub diary anyone can watch in real time
- Full CI/CD pipeline from day 1 — production engineering, not a toy
- Touches 9+ engineering disciplines in one project
- Every mechanic has a clear reason — explains itself well in an interview

**Cost to build:** $0 infrastructure · time only

---

*Update this file directly instead of regenerating in chat — saves tokens and keeps history clean.*
