# SamBot

AI-powered SDLC workflow automation — connects GitHub Projects V2, a custom coding agent (Claude Sonnet 4.5), and Slack.

## What It Does

1. **Polls a GitHub Projects V2 board** for stories moved to "In Progress"
2. **Runs a multi-pass AI coding agent** that reads the codebase, writes code + tests, and iterates until all tests pass
3. **Asks clarifying questions via Slack** when blocked on business or technical decisions
4. **Creates well-formed PRs** targeting `develop` with Claude-generated descriptions
5. **Streams progress** to a dedicated Slack channel
6. **Compresses new learnings** into persistent project memory after each job

## How It Works

```
Poller detects story → "In Progress"
  → Create branch (feature/<num>-slug or bug/<num>-slug)
  → Agent loop (up to N passes):
      1. Analyze story + memory + codebase
      2. Write/modify code + tests (Claude tool use)
      3. Run pytest
      4. If tests fail → analyze errors → next pass
      5. If blocked → ask question via Slack → wait → continue
  → All tests pass → commit + push
  → Create PR → develop
  → Compress new facts → update MEMORY.md
```

## Quick Start

```bash
# Clone and setup
cp .env.example .env
# Edit .env with your tokens

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
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `SAMBOT_POLL_INTERVAL` | Seconds between GitHub poll cycles | `30` |
| `SAMBOT_MAX_AGENT_PASSES` | Max coding passes per story | `5` |
| `SAMBOT_QUESTION_TIMEOUT_MINUTES` | Slack Q&A timeout | `30` |
| `SAMBOT_BASE_BRANCH` | Base branch for PRs | `develop` |

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
├── slack/
│   ├── app.py           # Bolt app setup
│   ├── commands.py      # /sambot slash commands
│   ├── progress.py      # Progress streaming
│   └── questions.py     # Agent ↔ human Q&A
├── agent/
│   ├── loop.py          # Multi-pass agent orchestration
│   ├── coder.py         # Claude tool-use coding
│   ├── memory.py        # Persistent memory + compression
│   ├── tools.py         # Tool definitions & executor
│   └── test_runner.py   # pytest execution & parsing
├── llm/
│   ├── client.py        # Anthropic client (memory-aware)
│   └── prompts.py       # System prompts
└── jobs/
    └── worker.py        # RQ job pipeline
```

## Architecture

See [PLAN.md](PLAN.md) for the full architecture and phased implementation plan.
See [MEMORY.md](MEMORY.md) for current project state and AI context.

## License

Apache 2.0
