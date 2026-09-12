# claude-code-speech

Claude Code reads its replies aloud, but only the ones you actually spoke. Type
a question and you get the usual silent, structured answer. Speak a question and
you get spoken prose back.

## Install

```
/plugin marketplace add <your-github-user>/claude-code-speech
/plugin install speech@speech-marketplace
```

Then run this once:

```
/speech-setup
```

That creates a small virtualenv and installs
[`edge-tts`](https://github.com/rany2/edge-tts), which gives you Microsoft's
neural voices for free with no account, though it does need a network
connection. Skip the setup step and the plugin falls back to whatever voice your
operating system already ships with.

## Commands

| Command | What it does |
|---|---|
| `/speech` | Show the current mode |
| `/speech auto` | Speak only messages that look dictated (default) |
| `/speech on` | Speak every reply |
| `/speech off` | Never speak |
| `/shush` | Stop talking immediately, mid sentence |
| `/speech-setup` | Install the neural voice |
| `/speech-status` | Show which backend is active |

Sending any message also silences whatever is currently playing.

## How auto mode decides

Claude Code's transcript marks dictated messages as `typed`, because dictation
types into the prompt box before submitting. There is no flag to read, so the
plugin judges from the shape of the text instead. Fillers like `um` and `like,`,
false starts and run on sentences all read as speech, while backticks, file
paths, flags, `snake_case` and markdown read as typing.

Short ambiguous messages such as "yes" or "do that" never flip the decision.
They inherit whatever the last clear message decided, so the mode won't flicker
on you.

## What it strips before speaking

Code blocks, URLs, markdown syntax and file paths are all cleaned up before the
text reaches the voice. `src/small_vector.hpp` is read as "small vector" rather
than "s r c slash small underscore vector dot h p p". Long replies are cut at a
sentence boundary.

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_SPEECH_VOICE` | `en-US-AndrewNeural` | Any edge-tts voice |
| `CLAUDE_SPEECH_RATE` | `+8%` | Speaking speed |
| `CLAUDE_SPEECH_MAX_CHARS` | `450` | Cut-off length |
| `CLAUDE_SPEECH_HOME` | `~/.claude/speech` | Where state lives |

List the available voices with
`~/.claude/speech/.venv/bin/edge-tts --list-voices`. The conversational ones
(Andrew, Brian, Ava, Emma) sound best.

## Platform support

| Platform | Neural voice | Fallback |
|---|---|---|
| WSL | edge-tts → Windows MediaPlayer | Windows SAPI |
| macOS | edge-tts → `afplay` | `say` |
| Linux | edge-tts → `mpg123`/`ffplay`/`mpv`/`cvlc` | `spd-say`/`espeak` |

On Linux you'll need one of those mp3 players installed before the neural voice
will work. Run `/speech-status` and it tells you what it found.

Everything here was developed and tested on WSL2. The macOS and Linux paths are
implemented but have not been tested on those platforms.

## Design notes

The Stop hook returns in about 40ms, because all the real work happens in a
detached child process and Claude Code is never left waiting on it. Time to
first audio is roughly 0.5s. The audio player starts first and waits for the
file to appear, so its own startup overlaps with synthesis. The file is then
renamed into place atomically, which means the player can never read a half
written file.
