---
allowed-tools: Bash(python3:*)
description: Show or set spoken-reply mode (auto | on | off)
argument-hint: "[auto|on|off]"
---
Speech mode is now: !`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/speak.py" --mode $ARGUMENTS`

Tell the user the current mode in one short sentence. Do not use any tools.
Meanings: auto = speak only messages that look dictated; on = speak every
reply; off = never speak.
