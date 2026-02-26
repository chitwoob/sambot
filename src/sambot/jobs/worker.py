"""RQ worker and job pipeline.

Complete story processing pipeline:
1. Fetch issue from GitHub
2. Load memory
3. Create branch from develop
4. Run agent loop (multi-pass)
5. Commit, push, create PR (if tests pass)
6. Update issue and project board
7. Compress and save new memory
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def process_story(issue_number: int) -> dict:
    """
    Background job: process a story end-to-end.

    This is the main entry point called by RQ.

    Pipeline:
    1. Fetch issue details from GitHub
    2. Load project memory (MEMORY.md)
    3. Create feature/bug branch from develop
    4. Run agent loop (Claude multi-pass with tool use)
    5. Agent writes code + tests, runs pytest
    6. If tests pass → commit, push, create PR into develop
    7. Comment on issue, move project item to "In Review"
    8. Compress new facts → update MEMORY.md
    """
    logger.info("job.process_story.start", issue_number=issue_number)

    # TODO Phase 3: Wire up the full pipeline
    # For now, the individual components are built and tested:
    # - agent/loop.py: AgentLoop.run()
    # - agent/memory.py: MemoryManager + compress_memory()
    # - agent/test_runner.py: TestRunner.run()
    # - github/pr.py: PRManager (branch, PR, comments)
    # - llm/client.py: LLMClient (memory-aware)
    # - slack/questions.py: SlackQuestionHandler

    return {"issue_number": issue_number, "status": "not_yet_wired"}
