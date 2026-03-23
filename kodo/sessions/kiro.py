"""Amazon Kiro CLI session using kiro-cli subprocess."""

from __future__ import annotations

from pathlib import Path

from kodo import log
from kodo.models import KIRO_DEFAULT
from kodo.sessions.base import QueryResult, SubprocessSession, classify_session_error


class KiroSession(SubprocessSession):
    _session_label = "kiro"

    def __init__(
        self,
        model: str = KIRO_DEFAULT,
        system_prompt: str | None = None,
        resume_session: bool = False,
        timeout_s: int = 7200,
    ):
        super().__init__(model, system_prompt, timeout_s=timeout_s)
        self._resume_next = resume_session
        self._has_queried = False

    def clone(self) -> "KiroSession":
        """Create a fresh session with the same config but no state."""
        return KiroSession(
            model=self.model,
            system_prompt=self.system_prompt,
            timeout_s=self._timeout_s,
        )

    @property
    def cost_bucket(self) -> str:
        return "kiro_subscription"

    @property
    def session_id(self) -> str | None:
        # Kiro CLI uses --resume for last session; no explicit session ID.
        return "last" if self._has_queried else None

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="kiro",
            model=self.model,
            queries_before=self._stats.queries,
        )
        self._resume_next = False
        self._has_queried = False
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        prompt = self._prepend_system_prompt(prompt)

        cmd = [
            "kiro-cli",
            "chat",
            prompt,
            "--no-interactive",
            "--trust-all-tools",
            "--wrap",
            "never",
        ]

        if self.model != "default":
            cmd.extend(["--model", self.model])

        if self._resume_next:
            cmd.append("--resume")

        log.emit(
            "session_query_start",
            session="kiro",
            model=self.model,
            prompt=prompt,
            resume=self._resume_next,
            project_dir=str(project_dir),
        )

        stdout_text = ""

        def _parse_stdout(proc):
            nonlocal stdout_text

            stdout_text = proc.stdout.read()
            result_text = stdout_text.strip() if stdout_text else ""
            if result_text:
                self._stats.touch()

            # Kiro CLI does not report token usage.
            return result_text, 0, 0

        r = self._query_template(cmd, cwd=str(project_dir), parse_stdout=_parse_stdout)
        if isinstance(r, QueryResult):
            log.emit(
                "session_query_end",
                session="kiro",
                elapsed_s=r.elapsed_s,
                is_error=True,
                error=r.text,
            )
            return r

        is_error = r.returncode != 0
        result_text = r.result_text

        if not result_text and not is_error and stdout_text.strip():
            result_text = "[completed, no text response]"

        if is_error and not result_text:
            hint = classify_session_error(
                r.returncode,
                r.stderr_text,
                stdout_text,
                "kiro",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            result_text = hint or r.stderr_text

        self._has_queried = True
        self._resume_next = True

        conv_file = None
        if stdout_text:
            conv_file = log.save_conversation(
                f"kiro_{id(self) % 10000:04d}",
                self._stats.queries,
                [{"raw_stdout": stdout_text}],
            )

        log.emit(
            "session_query_end",
            session="kiro",
            model=self.model,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            session_id=self.session_id,
            returncode=r.returncode,
            response_text=result_text,
            conversation_log=conv_file,
        )

        text_out = result_text or ""
        return QueryResult(
            text=text_out,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
        )
