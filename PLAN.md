# SamBot — SDLC Workflow Automation

## Vision

SamBot automates the software development lifecycle by connecting GitHub Projects, a custom AI coding agent (Claude Sonnet 4.5), and Slack. A developer assigns a story on a GitHub Project board → SamBot's agent picks it up, asks clarifying questions via Slack, generates code changes in a multi-pass loop, ensures tests pass, creates a well-formed PR into `develop`, and streams progress to Slack in real time.

---

## Architecture Overview

```
┌─────────────┐        ┌──────────────────┐        ┌──────────────┐
│  Slack App   │◄──────►│    SamBot Core    │◄──────►│  GitHub API  │
│  (Bolt SDK)  │        │  (FastAPI + RQ)   │        │ (PyGitHub /  │
│              │        │                   │        │  GraphQL)    │
│ • Create     │        │ • Webhook handler │        │              │
│   tickets    │        │ • Job runner      │        │ • Projects V2│
│ • Answer     │        │ • Coding Agent    │        │ • Issues     │
│   agent Q's  │        │ • Memory manager  │        │ • PRs        │
│ • View       │        │ • Test runner     │        │ • Branches   │
│   progress   │        │ • LLM client      │        └──────────────┘
└─────────────┘        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │   Anthropic API   │
                        │   Claude Sonnet   │
                        │       4.5         │
                        │ • Code generation │
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
│       │   ├── webhooks.py    # Webhook event handlers
│       │   └── pr.py          # PR creation & management
│       ├── slack/             # Slack interactions
│       │   ├── app.py         # Bolt app setup
│       │   ├── commands.py    # Slash commands
│       │   ├── views.py       # Modal views
│       │   ├── progress.py    # Agent progress streaming
│       │   └── questions.py   # Agent ↔ human Q&A
│       ├── agent/             # Custom AI coding agent
│       │   ├── loop.py        # Multi-pass agent loop
│       │   ├── coder.py       # Code generation via Claude
│       │   ├── memory.py      # Persistent memory + compression
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
- **Project memory** (`MEMORY.md`): Persistent facts about the project
- **Per-job context**: Story + conversation history
- **Compression**: After each job, Claude compresses new learnings into memory
- **Inclusion**: Every LLM call gets current memory as system context

### 4. Branch Strategy
- Base branch: `develop`
- Feature branches: `feature/<issue-num>-<slug>`
- Bug branches: `bug/<issue-num>-<slug>`
- PRs always target `develop`
- Determined by issue labels (label "bug" → bug/, else feature/)

### 5. Test Gating
- Agent MUST run tests after making changes
- PR only created if all tests pass
- PRs must include tests for new functionality
- Agent iterates until tests pass (up to max passes)

### 6. Slack Q&A
- Agent can ask technical or business questions via Slack
- Questions posted to progress channel in a thread
- Agent pauses until human responds (or timeout)
- Response fed back into agent context

---

## Workflow: Story → PR

```
1. Trigger: Story → "In Progress" OR `/sambot start <issue>`
2. Fetch issue + load MEMORY.md
3. Create branch from develop: feature/<num>-slug
4. Agent loop (multi-pass):
   a. Analyze story + memory + code
   b. Generate/modify code + tests
   c. Run pytest
   d. If fail → fix → repeat
   e. If blocked → Slack Q&A → continue
5. All tests pass → commit + push
6. Claude generates PR description
7. Create PR → develop
8. Comment on issue, move to "In Review"
9. Compress new facts → update MEMORY.md
```
