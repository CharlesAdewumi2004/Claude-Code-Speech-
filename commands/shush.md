---
allowed-tools: Bash(python3:*)
description: Stop Claude speaking right now
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --hush`

Reply with exactly "Stopped." and nothing else. Do not use any tools.
