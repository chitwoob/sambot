# SamBot — AI Memory File

> Persistent context for AI assistants working on this project.
> Updated as the project evolves. Read this first for orientation.

---

## Project State

**Current Phase:** Phase 2 — Custom Agent Core + 3-Channel Architecture  
**Last Updated:** 2026-02-26  
**Status:** Multi-channel Slack, backlog agent, centralized prompts, per-agent memory

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
3. **Custom agent with tool use** — Claude calls tools: read_file, write_file, list_dir, run_tests, ask_question
4. **Memory-aware LLM** — Every call includes per-agent memory via `build_system_prompt()`
5. **Multi-pass loop** — Agent iterates: code → test → fix → repeat (max 5 passes)
6. **Slack Q&A** — Agent posts questions to `#sambot-questions`, waits for human answers
7. **Test gating** — PR only created after all tests pass; PRs include tests
8. **Branch strategy** — develop → feature/<num>-slug or bug/<num>-slug
9. **Memory compression** — After each job, new facts compressed within token budget
10. **GraphQL for Projects V2** — Required by GitHub (no REST for v2 projects)
11. **Slack Socket Mode** — No public URL needed for Slack events
12. **Polling over webhooks** — Runs behind NAT/firewall; no inbound connectivity needed
13. **3 Slack channels** — Backlog (story building), Questions (agent Q&A), Progress (status)
14. **Centralized system prompts** — All prompts in `llm/prompts.py` with shared preamble + `{memory}` injection
15. **Per-agent memory** — Each agent has its own `MemoryManager` with configurable `max_tokens` budget

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

---

## Notes for Future Sessions

- The full plan is in `PLAN.md` — read it for the phased roadmap
- When adding a new module, update the File Map above
- When making an architectural decision, add it to the Decisions Log
- Keep this file under 500 lines — archive old sections if needed
- All LLM calls MUST include memory context (see llm/client.py)
- Agent tools are defined in agent/tools.py — add new tools there
