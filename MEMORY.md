# SamBot — AI Memory File

> Persistent context for AI assistants working on this project.
> Updated as the project evolves. Read this first for orientation.

---

## Project State

**Current Phase:** Phase 2 — Custom Agent Core  
**Last Updated:** 2026-02-26  
**Status:** Replacing Aider with custom multi-pass agent

---

## What Is SamBot?

SamBot is a Python-based SDLC automation tool that:
1. Reads stories from a GitHub Projects V2 board
2. Uses a custom AI coding agent (Claude Sonnet 4.5) to implement the story
3. Runs tests in a multi-pass loop until they pass
4. Asks clarifying questions to humans via Slack when blocked
5. Creates well-formed PRs with Claude-generated descriptions targeting `develop`
6. Streams progress to a dedicated Slack channel
7. Compresses new learnings into persistent project memory

---

## Stack Summary

- **Language:** Python 3.12+
- **Framework:** FastAPI (API / health) + slack-bolt (Slack)
- **Event Detection:** Polling (GitHub Projects V2 board, configurable interval)
- **Task Queue:** Redis + RQ
- **Database:** SQLite via SQLModel
- **LLM:** Anthropic Claude Sonnet 4.5 (via `anthropic` SDK)
- **AI Coding:** Custom multi-pass agent (not Aider)
- **GitHub:** PyGitHub (REST) + httpx (GraphQL for Projects V2)
- **Container:** Docker Compose (app + Redis)
- **Testing:** pytest + pytest-asyncio
- **Linting:** ruff

---

## Key Architectural Patterns

1. **Config via Pydantic Settings** — All config from env vars, typed and validated
2. **Background jobs via RQ** — Agent runs are long; FastAPI stays responsive
3. **Custom agent with tool use** — Claude calls tools: read_file, write_file, list_dir, run_tests, ask_question
4. **Memory-aware LLM** — Every call includes compressed MEMORY.md as system context
5. **Multi-pass loop** — Agent iterates: code → test → fix → repeat (max 5 passes)
6. **Slack Q&A** — Agent posts questions to Slack, waits for human answers
7. **Test gating** — PR only created after all tests pass; PRs include tests
8. **Branch strategy** — develop → feature/<num>-slug or bug/<num>-slug
9. **Memory compression** — After each job, new facts compressed into MEMORY.md
10. **GraphQL for Projects V2** — Required by GitHub (no REST for v2 projects)
11. **Slack Socket Mode** — No public URL needed for Slack events
12. **Polling over webhooks** — Runs behind NAT/firewall; no inbound connectivity needed

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
├── slack/           — All Slack interactions
│   ├── app.py       — Bolt app setup
│   ├── commands.py  — Slash commands
│   ├── views.py     — Modal views
│   ├── progress.py  — Progress streaming
│   └── questions.py — Agent Q&A (post question, collect answer)
├── agent/           — Custom AI coding agent
│   ├── loop.py      — Multi-pass agent orchestration
│   ├── coder.py     — Code gen via Claude tool use
│   ├── memory.py    — Persistent memory load/save/compress
│   ├── tools.py     — Tool definitions (read/write/list/test/ask)
│   └── test_runner.py — pytest execution & result parsing
├── llm/             — LLM interactions
│   ├── client.py    — Anthropic client wrapper (memory-aware)
│   └── prompts.py   — System prompts and templates
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

---

## Notes for Future Sessions

- The full plan is in `PLAN.md` — read it for the phased roadmap
- When adding a new module, update the File Map above
- When making an architectural decision, add it to the Decisions Log
- Keep this file under 500 lines — archive old sections if needed
- All LLM calls MUST include memory context (see llm/client.py)
- Agent tools are defined in agent/tools.py — add new tools there
