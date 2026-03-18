"""Guards for os.environ mutations shared across Claude components.

ClaudeSession and ClaudeCodeOrchestrator both pop/restore ANTHROPIC_API_KEY
to control billing. This lock prevents races when they run concurrently.
"""

import threading

anthropic_env_lock = threading.Lock()
