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

## A stop button

`/shush` stops playback, but so does anything else you send: the plugin cuts
the audio the moment you submit a prompt, so even a single character and enter
will do it. That covers most cases without a shortcut.

For a real key, bind one in your terminal rather than in Claude Code. Claude
Code's own keybindings map keys to a fixed set of built in actions and none of
them runs a slash command, so the binding has to live a layer below. In Windows
Terminal, add an action that types the command for you:

```json
"actions": [
  { "command": { "action": "sendInput", "input": "/speech:shush\r" },
    "id": "User.shushClaude", "name": "Shush Claude" }
],
"keybindings": [
  { "id": "User.shushClaude", "keys": "ctrl+shift+s" }
]
```

The `\r` is what presses enter. Other terminals have an equivalent: iTerm2 calls
it "Send Text", and most Linux terminals expose it through their profile
shortcuts.

## Replies written for the ear

A reply written to be read never quite sounds right out loud, however much
markdown you strip out of it afterwards. So when the plugin knows it is about to
speak, it says so before the answer is written. A hook on your prompt asks for
spoken prose: a few sentences, no headings or bullet lists, file paths described
rather than spelled out, and discussion in place of a wall of code.

The work still happens. Claude still edits files and runs commands when you ask,
it just tells you what it did instead of reading the diff back to you. When an
answer genuinely needs code, it offers it rather than reciting it.

This uses the same judgement that decides whether to speak at all, so a typed
message is untouched. Set `CLAUDE_SPEECH_PRIME=0` to turn it off and go back to
speaking whatever would have been written anyway.

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
| `CLAUDE_SPEECH_MAX_CHARS` | `1200` | Cut-off length |
| `CLAUDE_SPEECH_HOME` | `~/.claude/speech` | Where state lives |
| `CLAUDE_SPEECH_REPLY_WAIT` | `2.0` | Seconds to wait for the reply to land |
| `CLAUDE_SPEECH_PRIME` | `1` | Ask for spoken-style replies |
| `CLAUDE_SPEECH_MODE` | unset | Override the mode for one terminal |

List the available voices with
`~/.claude/speech/.venv/bin/edge-tts --list-voices`. The conversational ones
(Andrew, Brian, Ava, Emma) sound best.

## A voice terminal of its own

The idea is two terminals, not one that switches. Your usual terminal stays
written: silent, dense, structured answers, and whichever model you normally
work in. A second terminal is the spoken one, and everything about it is set
before it starts.

The mode lives in a file, so every session would otherwise share it and both
terminals would talk. Set `CLAUDE_SPEECH_MODE` and it applies to one terminal
only, which is what keeps the two genuinely separate. Leave the shared mode on
`off` so only the launcher turns speech on.

```
voice() {
  CLAUDE_SPEECH_MODE=on claude --model opus --effort low --name voice "$@"
}
```

Effort matters more than the model for how quickly a reply arrives. Claude Code
defaults high because that suits writing code; conversation does well lower.

Pair it with tap-to-send dictation, so speaking and sending are one gesture:

```json
"voice": { "enabled": true, "mode": "tap", "autoSubmit": true }
```

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

The Stop hook returns in under 90ms, because all the real work happens in a
detached child process and Claude Code is never left waiting on it.

The reply is spoken in pieces rather than as one lump, so the first sound
arrives once a short opening has been synthesised instead of the whole answer.
Every piece is synthesised at the same time and handed to the player in order.
Doing them one after another is what leaves an audible gap: the next piece only
starts once the one before it is written, and a brief opening runs out before
it arrives. The opening is kept to a few seconds of speech for the same reason,
since a one word sentence buys no cover at all.

The player starts before the first piece exists and waits for it, so its own
startup overlaps synthesis too. Each piece is renamed into place atomically,
so the player can never read a half written file, and a marker file tells it
when there is nothing more coming.
