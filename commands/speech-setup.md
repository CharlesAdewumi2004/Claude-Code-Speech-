---
allowed-tools: Bash(python3:*)
description: Install the neural voice (one-time, ~2MB)
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --setup`

Summarise the result in one or two short sentences. If it failed, say that the
plugin still works with the system voice. Do not use any tools.
