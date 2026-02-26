# SamBot

SDLC workflow automation — connects GitHub Projects, Aider, Slack, and Claude.

## What It Does

1. **Picks up stories** from a GitHub Projects V2 board
2. **Generates code** via Aider (AI pair programming)
3. **Creates well-formed PRs** with Claude-generated descriptions
4. **Streams progress** to a dedicated Slack channel
5. **Manages tickets** through a Slack interface

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

## Architecture

See [PLAN.md](PLAN.md) for the full architecture and phased implementation plan.  
See [MEMORY.md](MEMORY.md) for current project state and AI context.

## License

Apache 2.0
