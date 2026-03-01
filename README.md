# SamBot

AI-powered SDLC workflow automation — connects GitHub Projects V2, a custom coding agent (Claude Sonnet 4.5), and Slack across three dedicated channels.

## What It Does

1. **Polls a GitHub Projects V2 board** for stories moved to "In Progress"
2. **Runs a multi-pass AI coding agent** that reads the codebase, writes code + tests, and iterates until all tests pass
3. **Builds & refines backlog stories** interactively in a dedicated Slack channel
4. **Asks clarifying questions via Slack** when blocked on business or technical decisions
5. **Creates well-formed PRs** targeting `develop` with Claude-generated descriptions
6. **Streams progress** to a dedicated Slack channel
7. **Compresses new learnings** into persistent per-agent memory after each job

## Slack Channels

SamBot uses three Slack channels, each for a distinct purpose:

| Channel | Env Var | Purpose |
|---------|---------|---------|
| `#sambot-backlog` | `SLACK_BACKLOG_CHANNEL` | Build & refine stories with the backlog agent |
| `#sambot-questions` | `SLACK_QUESTIONS_CHANNEL` | Coding agent asks humans clarifying questions |
| `#sambot-progress` | `SLACK_PROGRESS_CHANNEL` | Real-time status updates on story implementation |

## How It Works

```
Poller detects story → "In Progress"
  → Create branch (feature/<num>-slug or bug/<num>-slug)
  → Agent loop (up to N passes):
      1. Analyze story + per-agent memory + codebase
      2. Write/modify code + tests (Claude tool use)
      3. Run pytest
      4. If tests fail → analyze errors → next pass
      5. If blocked → ask question in #questions → wait → continue
  → All tests pass → commit + push
  → Create PR → develop
  → Compress new facts → update agent memory
```

## Quick Start

```bash
# Clone and setup
cp .env.example .env
# Edit .env with your tokens (see Configuration below)

# Run with Docker
docker compose up

# Or run locally for development
pip install -e ".[dev]"
uvicorn sambot.main:app --reload
```

## Configuration

All config is via environment variables (see [.env.example](.env.example)):

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_TOKEN` | GitHub personal access token | *(required)* |
| `GITHUB_REPO` | Target repo (`owner/repo`) | *(required)* |
| `GITHUB_PROJECT_NUMBER` | Projects V2 board number | `1` |
| `ANTHROPIC_API_KEY` | Anthropic API key | *(required)* |
| `SLACK_BOT_TOKEN` | Slack bot OAuth token | |
| `SLACK_APP_TOKEN` | Slack app-level token | |
| `SLACK_PROGRESS_CHANNEL` | Channel for progress updates | `sambot-progress` |
| `SLACK_QUESTIONS_CHANNEL` | Channel for agent Q&A | `sambot-questions` |
| `SLACK_BACKLOG_CHANNEL` | Channel for story building | `sambot-backlog` |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `SAMBOT_POLL_INTERVAL` | Seconds between GitHub poll cycles | `30` |
| `SAMBOT_MAX_AGENT_PASSES` | Max coding passes per story | `5` |
| `SAMBOT_QUESTION_TIMEOUT_MINUTES` | Slack Q&A timeout (minutes) | `30` |
| `SAMBOT_BASE_BRANCH` | Base branch for PRs | `develop` |
| `SAMBOT_MEMORY_MAX_TOKENS` | Token budget per agent memory | `2000` |

### GitHub Token (Fine-Grained PAT)

Create a **fine-grained personal access token** scoped to your target repository with these permissions:

**Repository permissions:**

| Permission | Access | Used For |
|------------|--------|----------|
| Contents | Read & Write | Create branches, push commits |
| Issues | Read & Write | Read issue details, post comments |
| Pull requests | Read & Write | Create PRs |
| Metadata | Read-only | Required by default |

**Organization permissions** (or Account permissions for personal projects):

| Permission | Access | Used For |
|------------|--------|----------|
| Projects | Read & Write | Poll project board, update item status |

> **Note:** Projects V2 permissions are **not** under Repository permissions — they are under Organization permissions (for org projects) or Account permissions (for user-owned projects).

### Slack App Setup

SamBot uses **Socket Mode** so no public URL is needed.

1. **Create a Slack app** at [api.slack.com/apps](https://api.slack.com/apps) → *Create New App* → *From scratch*
2. **Enable Socket Mode:**
   - *Settings → Socket Mode* → Toggle on
   - Generate an **App-Level Token** with the `connections:write` scope → copy it as `SLACK_APP_TOKEN` (starts with `xapp-`)
3. **Add Bot Token Scopes** under *OAuth & Permissions → Scopes → Bot Token Scopes*:
   - `chat:write` — Post messages to channels
   - `commands` — Handle `/sambot` slash commands
   - `channels:history` — Read messages in public channels (for Q&A replies)
   - `channels:read` — List and find channels
4. **Install the app** to your workspace under *OAuth & Permissions → Install to Workspace*
   - Copy the **Bot User OAuth Token** as `SLACK_BOT_TOKEN` (starts with `xoxb-`)
5. **Copy the Signing Secret** from *Settings → Basic Information → App Credentials* as `SLACK_SIGNING_SECRET`
6. **Create slash command** under *Features → Slash Commands*:
   - Command: `/sambot`
   - Description: `SamBot AI assistant`
   - Usage hint: `help | status | start <issue>`
7. **Create three Slack channels** and invite the bot to each:
   - `#sambot-progress` — Real-time status updates
   - `#sambot-questions` — Agent asks humans clarifying questions
   - `#sambot-backlog` — Build & refine stories interactively

**Environment variables summary:**

```bash
SLACK_BOT_TOKEN=xoxb-...        # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...        # App-Level Token (Socket Mode)
SLACK_SIGNING_SECRET=...        # From Basic Information
SLACK_PROGRESS_CHANNEL=sambot-progress
SLACK_QUESTIONS_CHANNEL=sambot-questions
SLACK_BACKLOG_CHANNEL=sambot-backlog
```

## System Prompts

All agent prompts live in a single file: `src/sambot/llm/prompts.py`. Every LLM call goes through `build_system_prompt()` which injects a shared preamble + project memory automatically.

### Prompt Architecture

```
┌─────────────────────────────────────┐
│  _SHARED_PREAMBLE                   │  ← Identity + project memory
│  "You are SamBot..."                │
│  {memory}  ← injected at runtime   │
├─────────────────────────────────────┤
│  Agent-specific prompt              │  ← One of the constants below
│  e.g. CODING_AGENT_SYSTEM           │
└─────────────────────────────────────┘
```

### Available Prompts

| Constant | Used By | Purpose |
|----------|---------|---------|
| `CODING_AGENT_SYSTEM` | Coding agent (`agent/loop.py`) | Multi-pass code implementation |
| `BACKLOG_AGENT_SYSTEM` | Backlog agent (`agent/backlog.py`) | Story building in Slack |
| `STORY_REFINEMENT_SYSTEM` | LLM client | Refining vague stories |
| `PR_DESCRIPTION_SYSTEM` | LLM client | Generating PR descriptions |
| `MEMORY_COMPRESSION_SYSTEM` | Memory module | Compressing facts within token budget |

### Editing System Prompts

To modify how an agent behaves, edit its constant in `src/sambot/llm/prompts.py`:

```python
# src/sambot/llm/prompts.py

# Change the shared preamble (affects ALL agents):
_SHARED_PREAMBLE = """\
You are **SamBot**, an AI assistant that automates software development \
lifecycle tasks.  Below is the current project memory...
{memory}
---
"""

# Change an individual agent's behaviour:
CODING_AGENT_SYSTEM = """\
You are an expert AI software engineering agent...
"""
```

### Adding a New Agent Prompt

1. Define a new constant in `prompts.py`:
   ```python
   MY_AGENT_SYSTEM = """\
   You are a specialist in ...
   """
   ```
2. Use it when calling the LLM:
   ```python
   from sambot.llm.prompts import MY_AGENT_SYSTEM, build_system_prompt

   system = build_system_prompt(MY_AGENT_SYSTEM, memory_content)
   response = llm_client.complete_raw(prompt, system=system)
   ```

The `{memory}` placeholder in the shared preamble is filled automatically — you never need to manually inject memory into agent prompts.

## Memory System

Each agent has its own persistent memory file managed by `MemoryManager`. Memory is compressed by Claude after each job to stay within a configurable token budget.

### Memory Files

| File | Agent | Purpose |
|------|-------|---------|
| `MEMORY.md` | Coding agent | Project architecture, conventions, decisions |
| `backlog_memory.md` | Backlog agent | Story patterns, labelling conventions, project context |

### How Memory Works

```
Agent completes a job
  → New facts extracted
  → compress_memory() called:
      Claude merges new facts into existing memory
      Removes redundant/outdated info
      Stays within max_tokens budget
  → Updated memory saved to file
  → Next LLM call automatically includes it via build_system_prompt()
```

### Token Budgets

Memory size is controlled by `SAMBOT_MEMORY_MAX_TOKENS` (default `2000`, ~8000 chars). This prevents unbounded growth and keeps LLM costs predictable.

```bash
# In .env — increase budget if agents need more context
SAMBOT_MEMORY_MAX_TOKENS=3000
```

The compression prompt tells Claude to stay under the budget. You can check if memory is over budget:

```python
from sambot.agent.memory import MemoryManager

memory = MemoryManager(max_tokens=2000)
memory.is_over_budget()  # True if current file exceeds ~8000 chars
```

### Adding a New Agent Memory

To give a new agent its own memory:

```python
from pathlib import Path
from sambot.agent.memory import MemoryManager

# Create a memory manager with a dedicated file and budget
memory = MemoryManager(
    memory_path=Path("my_agent_memory.md"),
    max_tokens=1500,  # smaller budget for a focused agent
)

# Load existing memory
content = memory.load()  # returns "" if file doesn't exist yet

# Save updated memory
memory.save("# My Agent Memory\n\n- fact one\n- fact two")

# Compress new facts into existing memory (uses Claude)
from sambot.agent.memory import compress_memory
updated = compress_memory(llm_client, current_memory, new_facts, max_tokens=1500)
memory.save(updated)
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/
ruff format src/ tests/
```

## Project Structure

```
src/sambot/
├── main.py              # FastAPI app, poller startup
├── config.py            # Pydantic Settings (env vars)
├── models.py            # SQLModel models (jobs, questions)
├── db.py                # Database engine & session
├── github/
│   ├── client.py        # REST + GraphQL client
│   ├── projects.py      # Projects V2 GraphQL ops
│   ├── poller.py        # Polls project board for changes
│   └── pr.py            # Branch & PR management
├── slack/               # 3 channels: backlog, questions, progress
│   ├── app.py           # Bolt app setup
│   ├── commands.py      # /sambot slash commands
│   ├── progress.py      # #progress — status streaming
│   └── questions.py     # #questions — agent ↔ human Q&A
├── agent/
│   ├── loop.py          # Multi-pass agent orchestration
│   ├── coder.py         # Claude tool-use coding
│   ├── backlog.py       # #backlog — story building agent
│   ├── memory.py        # Per-agent memory + compression
│   ├── tools.py         # Tool definitions & executor
│   └── test_runner.py   # pytest execution & parsing
├── llm/
│   ├── client.py        # Anthropic client (memory-aware)
│   └── prompts.py       # Centralized system prompts
└── jobs/
    └── worker.py        # RQ job pipeline
```

## Architecture

See [PLAN.md](PLAN.md) for the full architecture and phased implementation plan.
See [MEMORY.md](MEMORY.md) for current project state and AI context.

## License

Apache 2.0
