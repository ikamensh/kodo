"""Resume logic: inject saved session IDs into agent sessions."""

from __future__ import annotations

from kodo.orchestrators.types import ResumeState, TeamConfig


def inject_resume_sessions(
    team: TeamConfig,
    resume: ResumeState | None,
) -> None:
    """Inject resume session IDs into agents before starting a run.

    Inspects each agent's session type and sets the appropriate resume
    attribute (``resume_session_id``, ``_chat_id``, ``_session_id``, or
    ``_resume_next``).

    No-op when *resume* is ``None``.
    """
    if not resume:
        return

    from kodo.sessions.claude import ClaudeSession
    from kodo.sessions.codex import CodexSession
    from kodo.sessions.cursor import CursorSession
    from kodo.sessions.gemini_cli import GeminiCliSession

    for agent_name, sid in resume.agent_session_ids.items():
        agent = team.get(agent_name)
        if agent is None:
            continue
        sess = agent.session
        if isinstance(sess, ClaudeSession):
            sess.resume_session_id = sid
        elif isinstance(sess, CursorSession):
            sess._chat_id = sid
        elif isinstance(sess, CodexSession):
            sess._session_id = sid
        elif isinstance(sess, GeminiCliSession):
            sess._resume_next = True
