# SamBot — AI Memory File

> Persistent context for AI assistants working on this project.
> Updated as the project evolves. Read this first for orientation.

---

## Project State

**Current Phase:** Phase 3 — Coder Bot Wiring & Full Pipeline  
**Last Updated:** 2026-03-01  
**Status:** Implementing coder bot: ready-scan, Docker gen, permission system, branch mgmt, PR merge

---

## What Is SamBot?

SamBot is a Python-based SDLC automation tool that:
1. Reads stories from a GitHub Projects V2 board
2. Uses a custom AI coding agent (Claude Sonnet 4.5) to implement the story
3. Runs tests in a multi-pass loop until they pass
4. Asks clarifying questions to humans via Slack when blocked
5. Creates well-formed PRs with Claude-generated descriptions targeting `develop`
6. Streams progress to `#sambot-progress` Slack channel
7. Builds/refines stories interactively in `#sambot-backlog` channel
8. Posts coding questions in `#sambot-questions` channel
9. Compresses new learnings into persistent per-agent memory (token-budgeted)

---

## Stack Summary

- **Language:** Python 3.12+
- **Framework:** FastAPI (API / health) + slack-bolt (Slack)
- **Event Detection:** Polling (GitHub Projects V2 board, configurable interval)
- **Task Queue:** Redis + RQ
- **Database:** SQLite via SQLModel
- **LLM:** Anthropic Claude Sonnet 4.5 (via `anthropic` SDK)
- **AI Coding:** Custom multi-pass agent (not Aider)
- **AI Backlog:** Backlog agent for story building in Slack
- **Slack Channels:** 3 — `#sambot-progress`, `#sambot-questions`, `#sambot-backlog`
- **System Prompts:** Centralized in `llm/prompts.py` with shared preamble
- **Memory:** Per-agent with token budgets (default 2000 tokens)
- **GitHub:** PyGitHub (REST) + httpx (GraphQL for Projects V2)
- **Container:** Docker Compose (app + Redis)
- **Testing:** pytest + pytest-asyncio
- **Linting:** ruff

---

## Key Architectural Patterns

1. **Config via Pydantic Settings** — All config from env vars, typed and validated
2. **Background jobs via RQ** — Agent runs are long; FastAPI stays responsive
3. **Custom agent with tool use** — Claude calls tools: read_file, write_file, list_dir, run_tests, ask_question, run_command, search_files
4. **Memory-aware LLM** — Every call includes per-agent memory via `build_system_prompt()`
5. **Multi-pass loop** — Agent iterates: code → test → fix → repeat (max 5 passes)
6. **Slack Q&A** — Agent posts questions to `#sambot-questions`, waits for human answers
7. **Test gating** — PR only created after all tests pass; PRs include tests
8. **Branch strategy** — develop → feature/<num>-slug or bug/<num>-slug; NEVER push to develop or main directly
9. **Ready-scan poller** — Polls for items in "Ready" status, picks top-to-bottom (priority order), moves to In Progress
10. **Docker permission system** — Coder generates Docker/compose for unknown stacks, asks permission in Slack before running; permissions persist in DB
11. **PR merge via rebase** — On approval, merge feature→develop via rebase; complex merges get re-review
12. **Branch stacking** — If multiple stories in review, coder can stack feature branches
13. **Memory compression** — After each job, new facts compressed within token budget
14. **GraphQL for Projects V2** — Required by GitHub (no REST for v2 projects)
15. **Slack Socket Mode** — No public URL needed for Slack events
16. **Polling over webhooks** — Runs behind NAT/firewall; no inbound connectivity needed
17. **3 Slack channels** — Backlog (story building), Questions (agent Q&A), Progress (status)
18. **Centralized system prompts** — All prompts in `llm/prompts.py` with shared preamble + `{memory}` injection
19. **Per-agent memory** — Each agent has its own `MemoryManager` with configurable `max_tokens` budget

---

## File Map

```
src/sambot/
├── main.py          — FastAPI app, lifespan, poller startup
├── config.py        — Pydantic Settings (env vars)
├── models.py        — SQLModel models (jobs, questions)
├── db.py            — Database engine & session
├── github/          — All GitHub interactions
│   ├── client.py    — Authenticated GitHub client (REST + GraphQL)
│   ├── projects.py  — Projects V2 GraphQL operations
│   ├── poller.py    — Polls project board for status changes
│   └── pr.py        — PR creation & branch management
├── slack/           — All Slack interactions (3 channels)
│   ├── app.py       — Bolt app setup
│   ├── commands.py  — Slash commands
│   ├── views.py     — Modal views
│   ├── progress.py  — #progress — status streaming
│   └── questions.py — #questions — agent Q&A (post question, collect answer)
├── agent/           — Custom AI coding agent
│   ├── loop.py      — Multi-pass agent orchestration
│   ├── coder.py     — Code gen via Claude tool use
│   ├── backlog.py   — Backlog agent (story building in Slack)
│   ├── memory.py    — Per-agent memory load/save/compress (token-budgeted)
│   ├── tools.py     — Tool definitions (read/write/list/test/ask)
│   └── test_runner.py — pytest execution & result parsing
├── llm/             — LLM interactions
│   ├── client.py    — Anthropic client wrapper (memory-aware)
│   └── prompts.py   — Centralized system prompts (shared preamble + per-agent)
└── jobs/
    └── worker.py    — RQ worker & job pipeline
```

---

## Conventions

- **Imports:** Use absolute imports (`from sambot.config import settings`)
- **Async:** FastAPI routes are async; RQ jobs are sync (separate process)
- **Logging:** Use `structlog` bound loggers, one per module
- **Type hints:** Required everywhere, enforced by ruff
- **Tests:** Mirror `src/` structure under `tests/`, prefix with `test_`
- **Commits:** Conventional commits (`feat:`, `fix:`, `chore:`, etc.)
- **Branches:** feature/<num>-slug or bug/<num>-slug, PRs → develop

---

## Decisions Log

| Date       | Decision                                    | Rationale                          |
|------------|---------------------------------------------|------------------------------------|
| 2026-02-26 | Python over TypeScript                      | Rich ecosystem, team familiarity   |
| 2026-02-26 | Anthropic only (no multi-LLM)               | Simplicity, Claude quality         |
| 2026-02-26 | Self-hosted Docker                          | Full control, simple deployment    |
| 2026-02-26 | Single repo target                          | Scope management                   |
| 2026-02-26 | RQ over Celery                              | Simpler for our scale              |
| 2026-02-26 | SQLite over Postgres                        | Zero config, single-instance ok    |
| 2026-02-26 | Custom agent over Aider                     | Full control, memory, Slack Q&A    |
| 2026-02-26 | Claude Sonnet 4.5 for coding                | Best coding model available        |
| 2026-02-26 | Multi-pass agent loop                       | Self-correcting, test-driven       |
| 2026-02-26 | develop as base branch                      | Standard gitflow, protect main     |
| 2026-02-26 | Test gating for PRs                         | Quality assurance                  |
| 2026-02-26 | Memory compression after jobs               | Persistent learning, context mgmt  |
| 2026-02-26 | Polling over webhooks                       | Docker behind NAT, no inbound ports |
| 2026-02-26 | 3 Slack channels                            | Separation of concerns: backlog, Q&A, progress |
| 2026-02-26 | Centralized system prompts                  | One file for all agent prompts, shared preamble |
| 2026-02-26 | Per-agent memory with token budgets         | Prevent unbounded memory growth, control costs |
| 2026-03-01 | Ready-scan instead of In-Progress trigger   | Bot picks work from Ready queue by priority    |
| 2026-03-01 | Docker permission system                    | Safety — bot asks before running new Docker    |
| 2026-03-01 | Never push to develop/main directly         | All changes via feature branches + PR          |
| 2026-03-01 | Rebase merge strategy                       | Clean history, complex merges get re-review    |
| 2026-03-01 | Language-agnostic coder                     | Scan repo to detect stack, generate Docker     |
| 2026-03-01 | Persistent Docker permissions in DB         | Don't re-ask for already-approved Docker files |

---

## Recent Completions

**Story #38: Set up local development environment** (Completed)
- Branch: `feature/38-set-up-local-development-environment`, PR: #39
- Stack detected: Node.js/TypeScript with NestJS
- Files created: README.md, .env.example, init.sql, Docker configs, health endpoints, setup scripts, validation tests
- Passes: 1 (completed on first attempt)
- Key learnings: Agent successfully detected TypeScript/NestJS stack, generated appropriate Docker setup, created comprehensive dev environment with health checks and validation

---

## Notes for Future Sessions

- The full plan is in `PLAN.md` — read it for the phased roadmap
- When adding a new module, update the File Map above
- When making an architectural decision, add it to the Decisions Log
- Keep this file under 2000 tokens — archive old sections if needed
- All LLM calls MUST include memory context (see llm/client.py)
- Agent tools are defined in agent/tools.py — add new tools there
- Coder NEVER updates develop or main branches — feature branches only
- Docker permissions tracked in `DockerPermission` SQLModel table
- Poller scans for "Ready" status items, not "In Progress"
- Agent can detect and work with multiple tech stacks (Python, TypeScript/NestJS confirmed)