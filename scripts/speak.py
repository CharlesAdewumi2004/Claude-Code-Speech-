#!/usr/bin/env python3
"""Speak Claude Code's replies aloud.

Stop hook. Pulls the reply from the turn that just ended out of the transcript,
strips the things that don't read well aloud, and plays it.

Voice: edge-tts neural voices when available (free, no account, needs network),
falling back to whatever the OS ships with.

  --stop                  silence whatever is playing
  --hush                  silence, and skip narrating the next reply
  --mode [auto|on|off]    read or set the mode
  --setup                 create the venv and install edge-tts
  --status                report what's installed and which backend is live
  --play <textfile>       internal: synthesis + playback
  --prime                 internal: ask for a spoken-style reply
  (stdin = Stop payload)  speak the last message if the mode says so
"""
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

# ---- knobs (env-overridable so users don't edit the file) ----------------
VOICE = os.environ.get("CLAUDE_SPEECH_VOICE", "en-US-AndrewNeural")
RATE = os.environ.get("CLAUDE_SPEECH_RATE", "+8%")
MAX_CHARS = int(os.environ.get("CLAUDE_SPEECH_MAX_CHARS", "1200"))
CHARS_PER_SEC = 13.0           # rough read speed, used only as a safety cap
REPLY_WAIT = float(os.environ.get("CLAUDE_SPEECH_REPLY_WAIT", "2.0"))
REPLY_POLL = 0.05              # how often to re-read while the reply lands
PRIME = os.environ.get("CLAUDE_SPEECH_PRIME", "1") not in ("0", "false", "no")
# --------------------------------------------------------------------------

STATE_DIR = os.path.expanduser(
    os.environ.get("CLAUDE_SPEECH_HOME", "~/.claude/speech"))
VENV_DIR = os.path.join(STATE_DIR, ".venv")
VENV_PY = os.path.join(VENV_DIR, "bin", "python")
PID_FILE = os.path.join(STATE_DIR, "speaking.pid")
MODE_FILE = os.path.join(STATE_DIR, "mode")
STICKY_FILE = os.path.join(STATE_DIR, "sticky")
VERDICT_FILE = os.path.join(STATE_DIR, "verdict")
SKIP_FILE = os.path.join(STATE_DIR, "skip-once")
TEMP_CACHE = os.path.join(STATE_DIR, "wintemp")
WINPID_NAME = "cc_speak_current.pid"   # fixed name: --stop always finds it
TEMP_FILES = []                        # cleaned on exit, even when killed


def ensure_state_dir():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except OSError:
        pass


def host():
    """'wsl', 'macos' or 'linux'."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        rel = platform.uname().release.lower()
        if "microsoft" in rel and shutil.which("powershell.exe"):
            return "wsl"
        return "linux"
    return "linux"


HOST = host()

CODE_EXT = (
    "py|pyc|hpp|cpp|cc|cxx|h|c|js|jsx|ts|tsx|json|jsonl|md|txt|sh|bash|zsh|"
    "yml|yaml|toml|ini|cfg|conf|html|css|scss|rs|go|java|rb|php|xml|lock|"
    "sql|csv|log|env|gitignore|cmake"
)
# Dots may only appear BETWEEN word characters, so a sentence-ending period
# is never swallowed into a path match.
_SEG = r"\.?[\w~-]+(?:\.[\w-]+)*"
PATH_RE = re.compile(
    r"(?:~|\.{1,2})?/" + _SEG + r"(?:/" + _SEG + r")*/?"
    r"|" + _SEG + r"(?:/" + _SEG + r")+/?"
    r"|\b[\w-]+\.(?:" + CODE_EXT + r")\b"
    r"|(?<![\w.])\.(?:gitignore|gitattributes|env|bashrc|zshrc|profile)\b"
)

# Dictation leaves fillers and false starts behind; typing leaves code,
# paths, flags and markdown.
DICTATED_RE = [
    re.compile(r"\b(?:um+|uh+|erm|hmm)\b", re.I),
    re.compile(r",\s*like,|\blike,\s|\byou know\b|\bi mean\b", re.I),
    re.compile(r"\b(?:kinda|sorta|gonna|wanna|gotta|yeah|okay so)\b", re.I),
    re.compile(r"\b(\w+)\s+\1\b", re.I),
    re.compile(r"^\s*(?:so|okay|ok|right|well|and|but|see)\b[,\s]", re.I),
]
TYPED_RE = [
    re.compile(r"`|```"),
    re.compile(r"[{};]|=>|::|\+\+"),
    re.compile(r"(?:^|\s)(?:-{1,2}[a-z]|/[a-z])", re.I),
    re.compile(r"\b[\w-]+\.(?:" + CODE_EXT + r")\b"),
    re.compile(r"(?:^|\s)[~./][\w./-]*/"),
    re.compile(r"^\s*[-*+]\s+|\n\s*\d+\.\s+"),
    re.compile(r"\b\w+_\w+\b|\b[a-z]+[A-Z]\w*\b|\b\w+\(\)"),
]


# ---- text ----------------------------------------------------------------

def humanize_path(match):
    """~/.claude/hooks/speak-last-message.py -> 'speak last message'"""
    token = match.group(0).rstrip("/")
    base = token.rsplit("/", 1)[-1].lstrip(".")
    base = re.sub(r"\.(?:" + CODE_EXT + r")$", "", base)
    return re.sub(r"[-_.]+", " ", base).strip() or "that file"


def _iter_entries(transcript_path):
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _text_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def _last_human_index(entries):
    """Position of the most recent message the user actually sent.
    -1 when there isn't one, so callers can slice from 0."""
    idx = -1
    for i, entry in enumerate(entries):
        if entry.get("isMeta") or entry.get("turnCompanion"):
            continue
        if (entry.get("origin") or {}).get("kind") != "human":
            continue
        message = entry.get("message") or {}
        if message.get("role") != "user":
            continue
        text = _text_of(message).strip()
        if text and not text.startswith("<"):
            idx = i
    return idx


def last_user_text(transcript_path):
    entries = _iter_entries(transcript_path)
    idx = _last_human_index(entries)
    if idx < 0:
        return None
    return _text_of(entries[idx]["message"]).strip()


def reply_text(transcript_path, wait=REPLY_WAIT):
    """The reply from the turn that just ended, or None.

    Anchored to the last human message, because anything at or before it
    belongs to an older turn. Claude Code writes the transcript from a
    different process, so the closing text block can land a moment after
    the Stop hook fires; we wait for it rather than scan further back and
    speak the previous turn's reply. A turn that ends on a tool call has
    no text at all, and silence is the right answer there.
    """
    deadline = time.time() + wait
    while True:
        entries = _iter_entries(transcript_path)
        for entry in reversed(entries[_last_human_index(entries) + 1:]):
            message = entry.get("message") or {}
            if message.get("role") != "assistant":
                continue
            text = _text_of(message)
            if text.strip():
                return text
        if time.time() >= deadline:
            return None
        time.sleep(REPLY_POLL)


def looks_dictated(text):
    """True / False, or None when there isn't enough signal to tell.

    Claude Code's transcript marks dictated messages as 'typed' (dictation
    types into the prompt box), so there is no flag to read - only the shape
    of the text itself.
    """
    if not text:
        return None
    dict_hits = sum(bool(r.search(text)) for r in DICTATED_RE)
    typed_hits = sum(bool(r.search(text)) for r in TYPED_RE)
    words = len(text.split())
    if words > 25 and text.count(".") <= words / 25:
        dict_hits += 1
    if typed_hits:
        return False
    if dict_hits >= 2 or (dict_hits == 1 and words > 12):
        return True
    return None


def speakable(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = PATH_RE.sub(humanize_path, text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\|.*$", "", text, flags=re.M)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text).strip()
    if len(text) > MAX_CHARS:
        cut = text.rfind(". ", 0, MAX_CHARS)
        text = text[: cut + 1] if cut > MAX_CHARS // 3 else text[:MAX_CHARS]
    return text


# ---- mode ----------------------------------------------------------------

def read_mode():
    """The environment wins over the file, so one terminal can be a voice
    session while another stays silent. Without it both share one mode."""
    env = os.environ.get("CLAUDE_SPEECH_MODE", "").strip().lower()
    if env in ("auto", "on", "off"):
        return env
    try:
        with open(MODE_FILE) as fh:
            mode = fh.read().strip().lower()
        return mode if mode in ("auto", "on", "off") else "auto"
    except OSError:
        return "auto"


def decide(text):
    """Should a reply to this message be spoken? The sticky file carries
    the last clear verdict through ambiguous one-liners like "yes", so
    the mode doesn't flicker mid-conversation."""
    mode = read_mode()
    if mode != "auto":
        return mode == "on"
    verdict = looks_dictated(text)
    if verdict is None:            # ambiguous -> keep doing what we did
        try:
            with open(STICKY_FILE) as fh:
                return fh.read().strip() == "1"
        except OSError:
            return True
    ensure_state_dir()
    try:
        with open(STICKY_FILE, "w") as fh:
            fh.write("1" if verdict else "0")
    except OSError:
        pass
    return verdict


def should_speak(transcript_path):
    """Prefer the verdict the prompt hook recorded for this turn. It saw the
    message as you sent it, while the transcript reader has to find it again
    and skips anything starting with "<" - so the two could disagree. Consume
    it, so a stale verdict can never decide a later turn."""
    try:
        with open(VERDICT_FILE) as fh:
            recorded = fh.read().strip()
        os.unlink(VERDICT_FILE)
        if recorded in ("0", "1"):
            return recorded == "1"
    except OSError:
        pass
    return decide(last_user_text(transcript_path))


# ---- voice-mode priming --------------------------------------------------

VOICE_STYLE = """This reply is going to be read aloud, so write it to be heard
rather than read.

Keep it to a few sentences. Use plain spoken prose: no markdown, no headings, no
bullet lists, no tables, no code blocks in the reply text.

Name things the way you would say them out loud. Say "the reply function in the
speak script" rather than spelling out a file path, and describe what a command
does instead of reciting its flags.

Lean towards discussion and planning. Do whatever tool work the request needs,
then say what you did and what you would do next, rather than reproducing the
code you just wrote.

If the answer genuinely needs code or a long structured list, say so in a
sentence and offer it, instead of reading it out."""


def do_prime():
    """UserPromptSubmit hook. When this message is one we'd speak, ask for
    a reply shaped for the ear. Cheaper and better than writing a dense
    reply and stripping it afterwards, which is all speakable() can do."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    verdict = decide(payload.get("prompt") or "")
    ensure_state_dir()
    try:
        with open(VERDICT_FILE, "w") as fh:
            fh.write("1" if verdict else "0")
    except OSError:
        pass
    if not PRIME or not verdict:
        return
    json.dump({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": VOICE_STYLE}}, sys.stdout)


# ---- windows plumbing (WSL only) -----------------------------------------

def win_temp():
    """(wsl_path, windows_path) of the Windows temp dir. Cached on disk,
    because spawning PowerShell to ask costs ~0.3s on every message."""
    try:
        with open(TEMP_CACHE) as fh:
            wsl, win = fh.read().split("\n")[:2]
        if wsl and win and os.path.isdir(wsl):
            return wsl, win
    except (OSError, ValueError):
        pass
    try:
        win = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "Write-Output $env:TEMP"],
            capture_output=True, timeout=20,
        ).stdout.decode(errors="replace").strip()
        if not win:
            return None
        wsl = subprocess.run(["wslpath", "-u", win], capture_output=True,
                             timeout=10).stdout.decode(errors="replace").strip()
        if not os.path.isdir(wsl):
            return None
        ensure_state_dir()
        with open(TEMP_CACHE, "w") as fh:
            fh.write(wsl + "\n" + win + "\n")
        return wsl, win
    except (OSError, subprocess.SubprocessError):
        return None


# ---- stopping ------------------------------------------------------------

def stop_windows():
    """Kill the Windows player. Costs a taskkill.exe spawn (~0.3s), so it
    never runs on the hook's critical path - only in the detached child."""
    if HOST != "wsl":
        return
    tmp = win_temp()
    if not tmp:
        return
    pid_path = os.path.join(tmp[0], WINPID_NAME)
    try:
        with open(pid_path, "rb") as fh:
            # PowerShell may emit ascii or utf-16; drop NULs either way
            raw = fh.read().replace(b"\x00", b"").decode("latin-1")
        winpid = re.sub(r"\D", "", raw)
        if winpid:
            subprocess.run(["taskkill.exe", "/PID", winpid, "/F", "/T"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(pid_path)
    except OSError:
        pass


def stop_local():
    """Kill our own detached child (and on mac/linux, the player with it,
    since it's in the same process group). Pure signal, no subprocess."""
    try:
        with open(PID_FILE) as fh:
            os.killpg(int(fh.read().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    # never leave a stale pid behind - the number could be reused by an
    # unrelated process group and we'd signal the wrong thing next time
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def stop_all():
    stop_windows()
    stop_local()


# ---- synthesis and playback ----------------------------------------------

def synthesize(text, out_path):
    """edge_tts library -> mp3. True on success. Uses the library rather
    than the CLI: the CLI costs ~1.2s of startup we don't need."""
    try:
        import asyncio
        import edge_tts
    except ImportError:
        return False
    try:
        async def run():
            comm = edge_tts.Communicate(text, VOICE, rate=RATE)
            with open(out_path, "wb") as fh:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
        asyncio.run(run())
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1024
    except Exception:
        return False


MP3_PLAYERS = [
    ("afplay", ["afplay", "{f}"]),
    ("mpg123", ["mpg123", "-q", "{f}"]),
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "{f}"]),
    ("mpv", ["mpv", "--no-video", "--really-quiet", "{f}"]),
    ("cvlc", ["cvlc", "--play-and-exit", "--quiet", "{f}"]),
]


def find_player():
    for exe, argv in MP3_PLAYERS:
        if shutil.which(exe):
            return argv
    return None


def _neural_posix(text):
    """macOS / Linux: synthesise, then hand the file to a local player."""
    argv = find_player()
    if not argv:
        return False
    import tempfile
    base = os.path.join(tempfile.gettempdir(), "cc_speak_%d.mp3" % os.getpid())
    part = base + ".part"
    TEMP_FILES.extend((part, base))
    try:
        if not synthesize(text, part):
            return False
        os.replace(part, base)
        subprocess.run([a.replace("{f}", base) for a in argv],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        for f in (part, base):
            try:
                os.unlink(f)
            except OSError:
                pass


def _neural_wsl(text):
    """WSL: the Windows player starts first and waits for the file, so its
    ~0.3s startup overlaps synthesis instead of following it."""
    tmp = win_temp()
    if not tmp:
        return False
    wsl_dir, win_dir = tmp
    name = "cc_speak_%d.mp3" % os.getpid()
    wsl_mp3 = os.path.join(wsl_dir, name)
    wsl_part = wsl_mp3 + ".part"
    win_mp3 = win_dir.rstrip("\\") + "\\" + name
    win_pid = win_dir.rstrip("\\") + "\\" + WINPID_NAME
    cap = len(text) / CHARS_PER_SEC + 6
    TEMP_FILES.extend((wsl_part, wsl_mp3))
    ps = (
        f"$PID | Out-File -FilePath '{win_pid}' -Encoding ascii;"
        "Add-Type -AssemblyName presentationCore;"
        "$p=New-Object System.Windows.Media.MediaPlayer;"
        "$n=0;"
        f"while(-not (Test-Path '{win_mp3}')){{Start-Sleep -Milliseconds 20;"
        "$n++; if($n -gt 1500){exit 1}};"
        f"$p.Open([uri]'{win_mp3}');"
        "$n=0;"
        "while(-not $p.NaturalDuration.HasTimeSpan -and $n -lt 150)"
        "{Start-Sleep -Milliseconds 20; $n++};"
        f"$d={cap:.1f};"
        "if($p.NaturalDuration.HasTimeSpan)"
        "{$d=$p.NaturalDuration.TimeSpan.TotalSeconds};"
        "$p.Play(); Start-Sleep -Seconds ($d+0.3); $p.Close()"
    )
    player = None
    try:
        player = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not synthesize(text, wsl_part):
            player.kill()
            return False
        os.replace(wsl_part, wsl_mp3)   # atomic: player never sees a partial file
        player.wait(timeout=cap + 120)
        return True
    except (OSError, subprocess.SubprocessError):
        if player:
            try:
                player.kill()
            except OSError:
                pass
        return False
    finally:
        for f in (wsl_part, wsl_mp3):
            try:
                os.unlink(f)
            except OSError:
                pass


def speak_neural(text):
    return _neural_wsl(text) if HOST == "wsl" else _neural_posix(text)


def speak_builtin(text):
    """Whatever the OS ships with. Always reachable, no network needed."""
    try:
        if HOST == "wsl":
            ps = ("Add-Type -AssemblyName System.Speech;"
                  "$t=[Console]::In.ReadToEnd();"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  "$s.Rate=1;$s.Speak($t)")
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            proc.communicate(text.encode("utf-8"), timeout=300)
            return True
        if HOST == "macos" and shutil.which("say"):
            subprocess.run(["say", text], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=300)
            return True
        for exe, argv in (("spd-say", ["spd-say", "-w", "--", text]),
                          ("espeak-ng", ["espeak-ng", text]),
                          ("espeak", ["espeak", text])):
            if shutil.which(exe):
                subprocess.run(argv, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=300)
                return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


# ---- housekeeping --------------------------------------------------------

def sweep_stale(max_age=300):
    """Drop leftovers from children killed before they could tidy up.
    Runs in the detached child, never on the hook's critical path."""
    import tempfile
    import time
    cutoff = time.time() - max_age
    dirs = [tempfile.gettempdir()]
    if HOST == "wsl":
        tmp = win_temp()
        if tmp:
            dirs.append(tmp[0])
    for directory in dirs:
        try:
            for name in os.listdir(directory):
                if not name.startswith("cc_speak_"):
                    continue
                if not (name.endswith(".mp3") or name.endswith(".part")):
                    continue
                f = os.path.join(directory, name)
                try:
                    if os.path.getmtime(f) < cutoff:
                        os.unlink(f)
                except OSError:
                    pass
        except OSError:
            pass
    try:
        for name in os.listdir(STATE_DIR):
            if name.startswith("say_") and name.endswith(".txt"):
                f = os.path.join(STATE_DIR, name)
                try:
                    if os.path.getmtime(f) < cutoff:
                        os.unlink(f)
                except OSError:
                    pass
    except OSError:
        pass


def dispatch(transcript_path):
    """Hand off to a detached child so the hook returns immediately.

    The child resolves the reply text itself rather than being handed it.
    Waiting for the closing block to land is the slow part, and it has no
    business on the hook's critical path: a turn that ends on a tool call
    would otherwise stall Claude Code for the whole wait before giving up.
    """
    stop_local()          # cheap; the child does the Windows-side kill
    ensure_state_dir()
    # the venv interpreter has edge_tts importable; plain python3 falls back
    runner = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    proc = subprocess.Popen(
        [runner, os.path.abspath(__file__), "--play", transcript_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    with open(PID_FILE, "w") as fh:
        fh.write(str(proc.pid))


# ---- setup / status ------------------------------------------------------

def has_edge_tts():
    if not os.path.exists(VENV_PY):
        return False
    try:
        return subprocess.run([VENV_PY, "-c", "import edge_tts"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def do_setup():
    ensure_state_dir()
    print("Installing the neural voice into %s" % VENV_DIR)
    try:
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR],
                       timeout=300, check=True)
        subprocess.run([os.path.join(VENV_DIR, "bin", "pip"), "install", "-q",
                        "--disable-pip-version-check", "edge-tts"],
                       timeout=600, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print("Setup failed: %s" % exc)
        print("The plugin still works using your system voice.")
        return
    print("Done." if has_edge_tts() else "Installed, but edge_tts won't import.")
    do_status()


def do_status():
    player = find_player()
    print("platform      : %s" % HOST)
    print("mode          : %s" % read_mode())
    print("neural voice  : %s" % ("ready (%s)" % VOICE if has_edge_tts()
                                  else "not installed - run /speech-setup"))
    if HOST == "wsl":
        print("playback      : Windows MediaPlayer")
    else:
        print("playback      : %s" % (player[0] if player else
                                      "no mp3 player found"))
    print("system voice  : %s" % ("available" if HOST in ("wsl", "macos")
                                  or shutil.which("spd-say")
                                  or shutil.which("espeak-ng")
                                  or shutil.which("espeak")
                                  else "none found"))
    print("state         : %s" % STATE_DIR)


# ---- entry point ---------------------------------------------------------

def main():
    argv = sys.argv[1:]

    if argv and argv[0] in ("--stop", "--hush"):
        stop_all()
        if argv[0] == "--hush":
            ensure_state_dir()
            try:
                open(SKIP_FILE, "w").close()
            except OSError:
                pass
        return

    if argv and argv[0] == "--mode":
        if len(argv) > 1 and argv[1] in ("auto", "on", "off"):
            ensure_state_dir()
            try:
                with open(MODE_FILE, "w") as fh:
                    fh.write(argv[1])
            except OSError:
                pass
        print(read_mode())
        return

    if argv and argv[0] == "--prime":
        do_prime()
        return

    if argv and argv[0] == "--setup":
        do_setup()
        return

    if argv and argv[0] == "--status":
        do_status()
        return

    if len(argv) == 2 and argv[0] == "--play":
        transcript = argv[1]

        def _cleanup(signum, frame):
            for f in TEMP_FILES:
                try:
                    os.unlink(f)
                except OSError:
                    pass
            os._exit(0)

        signal.signal(signal.SIGTERM, _cleanup)
        signal.signal(signal.SIGINT, _cleanup)
        sweep_stale()
        stop_windows()   # off the critical path, but before we make noise
        text = reply_text(transcript)
        if not text:
            return       # a turn that ended on a tool call has nothing to say
        text = speakable(text)
        if not text:
            return
        try:
            if not speak_neural(text):
                speak_builtin(text)   # text is held in memory, so a failed
        except Exception:             # neural run can't starve the fallback
            pass
        return

    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if os.path.exists(SKIP_FILE):
        try:
            os.unlink(SKIP_FILE)
        except OSError:
            pass
        return
    path = payload.get("transcript_path")
    if not path or not should_speak(path):
        return
    dispatch(path)


if __name__ == "__main__":
    main()
