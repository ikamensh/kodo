"""Shared guard for os.environ mutations around Claude components.

ClaudeSession pops/restores ANTHROPIC_API_KEY to control billing; an
orchestrator-layer wrapper (in kodo proper) does the same.  A single
module-level lock prevents races when both run concurrently.
"""

import threading

anthropic_env_lock = threading.Lock()
