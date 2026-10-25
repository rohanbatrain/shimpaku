"""Command policy for the presenter's Junos CLI console (``/console``, ``api/routes_console.py``).

The console runs one Junos *operational-mode* command over the existing NETCONF session
(``dev.cli(cmd, warning=False)``, text output). It is a read surface: every change to a device
still goes through the guarded action path (policy, approval, commit confirmed, health check).

This module is the only gate between what the presenter types and what reaches the device, so it
is deliberately small and fails closed:

1. **Characters.** Printable ASCII only (0x20–0x7E), at most ``MAX_LEN`` characters. Anything else
   (newlines, tabs, NUL, unicode look-alikes such as a full-width ``ｒｅｑｕｅｓｔ`` or a Cyrillic
   ``ѕhow``) is refused before parsing, never normalised into something that runs. ``;`` `` ` ``
   ``$`` ``&`` ``<`` ``>`` ``\\`` ``'`` ``"`` ``{`` ``}`` are refused outright.
2. **Verbs.** Junos accepts any unique prefix of a word (``conf`` = ``configure``, ``req sys`` =
   ``request system``). The first word is resolved against the *full* operational verb table
   (``VERBS``), exactly as Junos would: an exact match wins, else the single verb it is a prefix
   of; ambiguous or unknown words are refused. Only ``show``, ``ping`` and ``traceroute`` are
   allowed; everything else in the table is refused with a reason. The command sent to the device
   uses the resolved full verb.
3. **Options** of ``ping`` and ``traceroute`` are resolved the same way against Junos' option
   tables; only listed options are allowed, with bounds (``count`` 1–10, inserted as ``count 5``
   when absent, so a ping always ends).
4. **Pipes.** Each ``| word`` is resolved against the full pipe table (``save`` is the only pipe
   word starting with ``s``, so ``| s /tmp/x`` is ``save`` and refused). Only filters that cannot
   write, run a command or hold the session are allowed: ``match``, ``except``, ``count``,
   ``last``, ``no-more``, ``find``, ``trim``.
5. ``show configuration`` is refused: the console is shown on a shared screen and the configuration
   carries password hashes and keys; the Device page shows the parts that matter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LEN = 256
MAX_OUTPUT = 64 * 1024  # bytes of device output returned; the rest is cut and flagged
TIMEOUT_S = 20.0
PING_COUNT_DEFAULT = 5
PING_COUNT_MAX = 10

# Every top-level operational-mode word in Junos, plus the configuration-mode words, so a prefix is
# resolved against the whole space (a prefix that is unique among allowed verbs only must not pass).
VERBS: dict[str, str | None] = {
    "show": None,
    "ping": None,
    "traceroute": None,
    "clear": "clears counters or state on the device",
    "configure": "configuration mode: changes go through the guarded action path",
    "file": "reads, copies or deletes files on the device",
    "help": "not needed here; use the quick commands or the Junos docs",
    "load": "loads configuration",
    "monitor": "needs an interactive terminal; use `show interfaces <if> extensive` instead",
    "mtrace": "multicast trace is not on the allow-list",
    "op": "runs an op script",
    "quit": "there is no shell to quit",
    "exit": "there is no shell to exit",
    "request": "`request` changes or reboots the device (support information included)",
    "restart": "restarts a daemon",
    "save": "writes a file on the device",
    "set": "changes configuration or CLI settings",
    "ssh": "opens a session to another host",
    "start": "`start shell` is a raw shell",
    "telnet": "opens a session to another host",
    "test": "runs device tests that can change state",
    # configuration-mode words (refused if typed in operational mode)
    "edit": "configuration mode",
    "delete": "configuration mode",
    "commit": "configuration mode",
    "rollback": "configuration mode",
    "activate": "configuration mode",
    "deactivate": "configuration mode",
    "annotate": "configuration mode",
    "copy": "configuration mode",
    "insert": "configuration mode",
    "rename": "configuration mode",
    "replace": "configuration mode",
    "wildcard": "configuration mode",
    "run": "configuration mode",
    "top": "configuration mode",
    "up": "configuration mode",
    "status": "configuration mode",
    "update": "configuration mode",
}
ALLOWED_VERBS = ("show", "ping", "traceroute")

# Junos pipe words (`| ?` on 23.x). value None = allowed filter.
PIPES: dict[str, str | None] = {
    "count": None, "except": None, "find": None, "last": None, "match": None, "no-more": None, "trim": None,
    "append": "writes a file", "compare": "compares against files", "display": "changes the output format",
    "hold": "holds the session open", "refresh": "re-runs the command forever", "request": "runs a request",
    "resolve": "DNS lookups", "save": "writes a file", "tee": "writes a file",
}

# Junos ping options. value = None (flag) | (min, max) integer bound | "word" (any safe token) | refusal str.
_PING: dict[str, object] = {
    "count": (1, PING_COUNT_MAX), "rapid": None, "no-resolve": None, "do-not-fragment": None, "inet": None,
    "inet6": None, "detail": None, "verbose": None, "size": (0, 1472), "ttl": (1, 64), "wait": (1, 5),
    "source": "word", "routing-instance": "word", "interface": "word", "logical-system": "word", "tos": (0, 255),
    "bypass-routing": "refused: bypass-routing", "interval": "refused: interval (flooding)",
    "loose-source": "refused: source routing", "strict-source": "refused: source routing",
    "strict": "refused: source routing", "record-route": "refused: record-route", "pattern": "refused: pattern",
    "mac-address": "refused: mac-address", "through": "refused: through",
}
_TRACE: dict[str, object] = {
    "no-resolve": None, "inet": None, "inet6": None, "as-number-lookup": None, "propagate-ttl": None,
    "ttl": (1, 30), "wait": (1, 5), "source": "word", "routing-instance": "word", "interface": "word",
    "logical-system": "word", "tos": (0, 255), "bypass-routing": "refused: bypass-routing",
    "gateway": "refused: gateway", "monitor": "refused: monitor needs a terminal", "mpls": "refused: mpls",
}

_BAD_CHARS = set(";`$&<>\\'\"{}")
_TOKEN = re.compile(r"^[A-Za-z0-9._:/\-*+=,@\[\]()^!?#%~]+$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:\-]{0,252}$")


class Refused(ValueError):
    """The command is not allowed. ``reason`` is safe to show the presenter."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Command:
    verb: str   # resolved full verb: show | ping | traceroute
    text: str   # the normalised command sent to the device (full verb, single spaces)


def resolve(word: str, table: dict) -> str:
    """Junos unique-prefix resolution: the exact word, else the one table word it prefixes."""
    w = word.lower()
    if w in table:
        return w
    hits = [k for k in table if k.startswith(w)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Refused(f"unknown word {word!r}")
    raise Refused(f"{word!r} is ambiguous ({', '.join(sorted(hits))}); type more of it")


def _check_chars(raw: str) -> str:
    if not isinstance(raw, str):
        raise Refused("command must be text")
    if len(raw) > MAX_LEN:
        raise Refused(f"command longer than {MAX_LEN} characters")
    for ch in raw:
        o = ord(ch)
        if o < 0x20 or o > 0x7E:
            raise Refused("only printable ASCII is accepted (no newlines, tabs or unicode look-alikes)")
        if ch in _BAD_CHARS:
            raise Refused(f"character {ch!r} is not accepted")
    s = " ".join(raw.split())
    if not s:
        raise Refused("empty command")
    return s


def _int_in(word: str, lo: int, hi: int, name: str) -> int:
    if not word.isdigit():
        raise Refused(f"{name} needs a number")
    v = int(word)
    if not lo <= v <= hi:
        raise Refused(f"{name} must be {lo}–{hi}")
    return v


def _options(verb: str, args: list[str], table: dict[str, object]) -> list[str]:
    """``<host> [option [value]]...`` with options resolved and bounded."""
    out: list[str] = []
    host: str | None = None
    seen: set[str] = set()
    i = 0
    while i < len(args):
        w = args[i]
        if host is None and _HOST.match(w) and not any(k.startswith(w.lower()) for k in table):
            host = w
            out.append(w)
            i += 1
            continue
        opt = resolve(w, table)
        spec = table[opt]
        if isinstance(spec, str) and spec.startswith("refused"):
            raise Refused(f"{verb} option {opt} is not allowed ({spec[9:]})")
        if opt in seen:
            raise Refused(f"{verb} option {opt} given twice")
        seen.add(opt)
        if spec is None:
            out.append(opt)
            i += 1
            continue
        if i + 1 >= len(args):
            raise Refused(f"{verb} option {opt} needs a value")
        val = args[i + 1]
        if isinstance(spec, tuple):
            out += [opt, str(_int_in(val, spec[0], spec[1], f"{verb} {opt}"))]
        else:
            if not _TOKEN.match(val):
                raise Refused(f"{verb} {opt}: bad value")
            out += [opt, val]
        i += 2
    if host is None:
        raise Refused(f"{verb} needs a host or address")
    if verb == "ping" and "count" not in seen:
        out += ["count", str(PING_COUNT_DEFAULT)]
    return out


def check(raw: str) -> Command:
    """Parse and allow one command, or raise ``Refused``."""
    s = _check_chars(raw)
    segments = [seg.strip() for seg in s.split("|")]
    head = segments[0].split()
    if not head:
        raise Refused("empty command")
    verb = resolve(head[0], VERBS)
    if verb not in ALLOWED_VERBS:
        raise Refused(f"`{verb}`: {VERBS[verb]}")
    rest = head[1:]
    for w in rest:
        if not _TOKEN.match(w):
            raise Refused(f"bad word {w!r}")
    if verb == "show":
        if not rest:
            raise Refused("show what? e.g. `show interfaces terse`")
        w0 = rest[0].lower()
        if len(w0) >= 2 and "configuration".startswith(w0):
            raise Refused("`show configuration` is refused on the console (it carries secrets on a shared "
                          "screen); use the Device page")
        body = ["show", *rest]
    else:
        body = [verb, *_options(verb, rest, _PING if verb == "ping" else _TRACE)]
    parts = [" ".join(body)]
    for seg in segments[1:]:
        words = seg.split()
        if not words:
            raise Refused("empty pipe")
        pw = resolve(words[0], PIPES)
        if PIPES[pw] is not None:
            raise Refused(f"`| {pw}`: {PIPES[pw]}")
        if pw in ("count", "no-more") and len(words) > 1:
            raise Refused(f"`| {pw}` takes no argument")
        if pw == "last" and (len(words) > 2 or (len(words) == 2 and not words[1].isdigit())):
            raise Refused("`| last` takes one number")
        if pw in ("match", "except", "find") and len(words) != 2:
            raise Refused(f"`| {pw}` takes one pattern without spaces")
        for w in words[1:]:
            if not _TOKEN.match(w):
                raise Refused(f"bad pipe argument {w!r}")
        parts.append(" ".join([pw, *words[1:]]))
    if verb != "show" and len(parts) > 1:
        raise Refused("pipes are only accepted after show")
    return Command(verb=verb, text=" | ".join(parts))


def cap_output(text: str) -> tuple[str, bool]:
    """At most ``MAX_OUTPUT`` UTF-8 bytes; True when cut."""
    b = (text or "").encode("utf-8", "replace")
    if len(b) <= MAX_OUTPUT:
        return text or "", False
    return b[:MAX_OUTPUT].decode("utf-8", "ignore"), True
