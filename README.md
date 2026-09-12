# claude-code-speech

Reads Claude Code's replies aloud with a neural voice — but only the ones you
*dictated*. Type a question and you get the usual silent, structured answer;
speak a question and you get spoken prose back.

## Install

```
/plugin marketplace add <your-github-user>/claude-code-speech
/plugin install speech@speech-marketplace
```

Then, once:

```
/speech-setup
```

That creates a small virtualenv and installs [`edge-tts`](https://github.com/rany2/edge-tts)
(Microsoft's neural voices — free, no account, needs network). Skip it and the
plugin falls back to your operating system's built-in voice.

## Commands

| Command | What it does |
|---|---|
| `/speech` | Show the current mode |
| `/speech auto` | Speak only messages that look dictated (default) |
| `/speech on` | Speak every reply |
| `/speech off` | Never speak |
| `/shush` | Stop talking *right now*, mid-sentence |
| `/speech-setup` | Install the neural voice |
| `/speech-status` | Show which backend is active |

Sending any message also silences whatever is currently playing.

## How "auto" decides

Claude Code's transcript marks dictated messages as `typed`, because dictation
types into the prompt box before submitting — so there is no flag to read. The
plugin judges from the shape of the text instead: fillers (`um`, `like,`),
false starts and run-on sentences read as speech; backticks, file paths, flags,
`snake_case` and markdown read as typing.

Short ambiguous messages ("yes", "do that") don't flip the decision — they
inherit whatever the last clear message decided, so it won't flicker on you.

## What it strips before speaking

Code blocks, URLs, markdown syntax, and file paths — `src/small_vector.hpp`
is read as "small vector", not "s r c slash small underscore vector dot h p p".
Long replies are cut at a sentence boundary.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_SPEECH_VOICE` | `en-US-AndrewNeural` | Any edge-tts voice |
| `CLAUDE_SPEECH_RATE` | `+8%` | Speaking speed |
| `CLAUDE_SPEECH_MAX_CHARS` | `450` | Cut-off length |
| `CLAUDE_SPEECH_HOME` | `~/.claude/speech` | Where state lives |

List voices: `~/.claude/speech/.venv/bin/edge-tts --list-voices`.
The conversational ones (Andrew, Brian, Ava, Emma) sound best.

## Platform support

| Platform | Neural voice | Fallback |
|---|---|---|
| WSL | edge-tts → Windows MediaPlayer | Windows SAPI |
| macOS | edge-tts → `afplay` | `say` |
| Linux | edge-tts → `mpg123`/`ffplay`/`mpv`/`cvlc` | `spd-say`/`espeak` |

On Linux you need one of those mp3 players installed for the neural voice;
`/speech-status` tells you what it found.

Developed and tested on WSL2. The macOS and Linux paths are implemented but
have not been tested on those platforms.

## Design notes

The Stop hook returns in ~40ms — all work happens in a detached child, so
Claude Code is never blocked. Time to first audio is ~0.5s, because the audio
player is started *first* and waits for the file, overlapping its own startup
with synthesis. The file is renamed into place atomically so the player can
never read a half-written file.
