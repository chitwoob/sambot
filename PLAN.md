# SamBot — SDLC Workflow Automation

## Vision

SamBot automates the software development lifecycle by connecting GitHub Projects, a custom AI coding agent (Claude Sonnet 4.5), and Slack. A developer assigns a story on a GitHub Project board → SamBot's agent picks it up, asks clarifying questions via Slack, generates code changes in a multi-pass loop, ensures tests pass, creates a well-formed PR into `develop`, and streams progress to Slack in real time.

---

## Architecture Overview

```
┌─────────────────┐    ┌──────────────────┐        ┌──────────────┐
│  Slack App      │◄──►│    SamBot Core    │◄──────►│  GitHub API  │
│  (Bolt SDK)     │    │  (FastAPI + RQ)   │        │ (PyGitHub /  │
│                 │    │                   │        │  GraphQL)    │
│ 3 Channels:     │    │ • Poller (polls   │        │              │
│ • #backlog      │    │   project board)  │        │ • Projects V2│
│   (story build) │    │ • Backlog Agent   │        │ • Issues     │
│ • #questions    │    │ • Coding Agent    │        │ • PRs        │
│   (agent Q&A)   │    │ • Memory manager  │        │ • Branches   │
│ • #progress     │    │ • Test runner     │        └──────────────┘
│   (status)      │    │ • LLM client      │
└─────────────────┘    └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │   Anthropic API   │
                        │   Claude Sonnet   │
                        │       4.5         │
                        │ • Code generation │
                        │ • Backlog stories │
                        │ • Story refinement│
                        │ • PR descriptions │
                        │ • Memory compress │
                        │ • Q&A decisions   │
                        └──────────────────┘
```

---

## Tech Stack

| Component           | Technology                          | Why                                                      |
|---------------------|-------------------------------------|----------------------------------------------------------|
| **Language**        | Python 3.12+                        | Rich ecosystem, async support                            |
| **Web Framework**   | FastAPI                             | Async, webhook handling, lightweight, auto-docs          |
| **Task Queue**      | Redis + RQ (Redis Queue)            | Simple background job processing for agent runs          |
| **Slack SDK**       | slack-bolt (Python)                 | Official Slack SDK, socket mode + events API             |
| **GitHub**          | PyGitHub + httpx (GraphQL)          | REST for Issues/PRs, GraphQL for Projects V2             |
| **LLM**            | anthropic (Python SDK)              | Direct Claude Sonnet 4.5 API access                      |
| **AI Coding**       | Custom Agent (multi-pass loop)      | Full control, memory-aware, Slack Q&A integrated         |
| **Database**        | SQLite (via SQLModel)               | Lightweight, zero-config, good enough for single-repo    |
| **Config**          | Pydantic Settings + .env            | Type-safe config, 12-factor app                          |
| **Containerization**| Docker Compose                      | Redis + SamBot in one `docker compose up`                |
| **Testing**         | pytest + pytest-asyncio             | Standard Python testing                                  |
| **Linting**         | ruff                                | Fast, all-in-one Python linter/formatter                 |

---

## Project Structure

```
sambot/
├── PLAN.md                    # This file — project roadmap
├── MEMORY.md                  # Dynamic memory for AI context
├── src/
│   └── sambot/
│       ├── main.py            # FastAPI app entry point
│       ├── config.py          # Pydantic settings
│       ├── models.py          # SQLModel DB models
│       ├── db.py              # Database setup
│       ├── github/            # GitHub interactions
│       │   ├── client.py      # REST + GraphQL client
│       │   ├── projects.py    # Projects V2 operations
│       │   ├── poller.py      # Polls project board for status changes
│       │   └── pr.py          # PR creation & management
│       ├── slack/             # Slack interactions (3 channels)
│       │   ├── app.py         # Bolt app setup
│       │   ├── commands.py    # Slash commands
│       │   ├── views.py       # Modal views
│       │   ├── progress.py    # #progress — agent status streaming
│       │   └── questions.py   # #questions — agent ↔ human Q&A
│       ├── agent/             # Custom AI coding agent
│       │   ├── loop.py        # Multi-pass agent loop
│       │   ├── coder.py       # Code generation via Claude
│       │   ├── backlog.py     # #backlog — story refinement agent
│       │   ├── memory.py      # Per-agent memory + compression
│       │   ├── tools.py       # Agent tools (read/write/list)
│       │   └── test_runner.py # Run tests, parse results
│       ├── llm/               # LLM interactions
│       │   ├── client.py      # Anthropic client (memory-aware)
│       │   └── prompts.py     # Prompt templates
│       └── jobs/
│           └── worker.py      # RQ worker & job definitions
└── tests/
```

---

## Key Design Decisions

### 1. Custom Agent over Aider
We use our own multi-pass coding agent instead of Aider:
- **Full control** over the loop, tool use, and decision making
- **Memory-aware**: every LLM call includes compressed project memory
- **Slack Q&A**: agent can pause and ask questions to humans
- **Test-gated**: agent runs tests and iterates until they pass
- **No subprocess coupling**: direct Anthropic SDK calls

### 2. Multi-Pass Agent Loop
```
PASS N:
 1. Claude analyzes story + memory + codebase
 2. Claude generates/modifies code + tests
 3. Run pytest → capture results
 4. If tests fail → analyze errors → PASS N+1
 5. If blocked → ask question via Slack → wait for answer → continue
 6. If all tests pass → done
 Max passes: configurable (default 5)
```

### 3. Memory System
- **Coding agent memory** (`MEMORY.md`): Persistent facts about the project
- **Backlog agent memory** (`backlog_memory.md`): Facts about stories and backlog
- **Per-job context**: Story + conversation history
- **Token budgets**: Each memory has a configurable max_tokens limit (default 2000)
- **Compression**: After each job, Claude compresses new learnings into memory within budget
- **Inclusion**: Every LLM call gets current memory via `build_system_prompt()`

### 4. Branch Strategy
- Base branch: `develop`
- Feature branches: `feature/<issue-num>-<slug>`
- Bug branches: `bug/<issue-num>-<slug>`
- PRs always target `develop` (or another feature branch, NEVER `main`)
- Determined by issue labels (label "bug" → bug/, else feature/)
- Coder NEVER pushes to `develop` or `main` directly
- Always starts from a clean `develop` branch
- Can stack feature branches when multiple stories are in review

### 5. Test Gating
- Agent MUST run tests after making changes
- PR only created if all tests pass
- PRs must include tests for new functionality
- Agent iterates until tests pass (up to max passes)

### 6. Slack Q&A
- Agent can ask technical or business questions via Slack
- Questions posted to `#sambot-questions` channel in a thread
- Agent pauses until human responds (or timeout)
- Response fed back into agent context

### 10. Ready-Scan Workflow
- Poller scans for items with "Ready" status on the GitHub Projects board
- Items picked in priority order (top-to-bottom as they appear on the board)
- Once picked, item moves to "In Progress"
- When done: moves to "In Review" (with PR posted to Slack) or "Blocked" if issues

### 11. Language-Agnostic Coder
- The coder does NOT assume any particular language or stack
- It scans the repo to discover the tech stack (package files, configs, etc.)
- It generates Dockerfile and docker-compose.yml for building and testing
- Before running any newly generated Docker files, it MUST ask for permission via Slack
- Permissions are persistent — once approved, the coder won't ask again for the same file
- All dev files (Dockerfiles, compose, etc.) are committed to the working repo

### 12. PR Merge on Approval
- When a PR is approved, coder merges into `develop` or another feature branch (NEVER `main`)
- All merges use rebase strategy
- If the merge/rebase is complex (conflicts), request a new review before completing
- If the merge is clean, automatically complete the merge into develop or the feature branch

### 7. Three Slack Channels
| Channel               | Purpose                                          |
|-----------------------|--------------------------------------------------|
| `#sambot-backlog`     | Build & refine stories interactively via backlog agent |
| `#sambot-questions`   | Coding agent asks humans clarifying questions    |
| `#sambot-progress`    | Real-time status updates on story implementation |

### 8. Centralized System Prompts
- All agent prompts defined in `llm/prompts.py`
- Shared preamble injected into every agent with `{memory}` placeholder
- `build_system_prompt(agent_prompt, memory)` combines preamble + memory + agent-specific prompt
- Keeps prompts maintainable in one place

### 9. Per-Agent Memory with Token Budgets
- Each agent has its own `MemoryManager` instance with a configurable `max_tokens` budget
- Coding agent uses `MEMORY.md`, backlog agent uses `backlog_memory.md`
- Compression prompt instructs Claude to stay within the token budget
- `is_over_budget()` check prevents unbounded memory growth
- Default budget: 2000 tokens (~8000 chars)

---

## Workflow: Story → PR

```
1. Trigger: Poller detects story in "Ready" status on project board
2. Pick the highest-priority item (top-to-bottom order)
3. Move item to "In Progress" on the project board
4. Fetch issue details + load MEMORY.md
5. Create clean branch from develop: feature/<num>-slug
   (OR stack on top of another feature branch if stories are queued)
6. Scan repo to detect stack/language (if first time)
7. Generate Dockerfile/docker-compose if not present
8. Ask permission in Slack to run new Docker files (persisted)
9. Agent loop (multi-pass):
   a. Analyze story + memory + code
   b. Generate/modify code + tests
   c. Run tests (via Docker if available)
   d. If fail → fix → repeat
   e. If blocked → ask question via Slack → continue
10. All tests pass → commit + push to feature branch
11. Claude generates PR description
12. Create PR → develop (or feature branch, never main)
13. Post PR link to Slack #sambot-progress
14. Move project item to "In Review"
15. Compress new facts → update MEMORY.md
16. On PR approval:
    a. Rebase merge into develop (or feature branch)
    b. If merge is complex → request new review
    c. If merge is clean → complete automatically
17. If blocked at any point → move to "Blocked" on project board
```
