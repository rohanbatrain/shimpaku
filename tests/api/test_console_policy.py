"""The console command policy (shimpaku.console_policy): allow-list, Junos prefix resolution,
pipes, characters. Pure; no device."""

from __future__ import annotations

import pytest

from shimpaku import console_policy as P

ALLOWED = {
    "show interfaces terse": "show interfaces terse",
    "  sh   ospf neighbor ": "show ospf neighbor",
    "SHOW bfd session": "show bfd session",
    "show vlans | match demo": "show vlans | match demo",
    "show log messages | last 20": "show log messages | last 20",
    "show interfaces terse | m ge- | count": "show interfaces terse | match ge- | count",
    "show route | no-more": "show route | no-more",
    "ping 10.1.0.1": "ping 10.1.0.1 count 5",
    "ping 10.1.0.1 count 10 rapid": "ping 10.1.0.1 count 10 rapid",
    "p 10.1.0.1 c 3": "ping 10.1.0.1 count 3",
    "ping count 2 leaf1": "ping count 2 leaf1",
    "traceroute 10.255.0.4 no-resolve": "traceroute 10.255.0.4 no-resolve",
    "tr 10.255.0.4": "traceroute 10.255.0.4",
}


@pytest.mark.parametrize("raw,sent", ALLOWED.items())
def test_allowed(raw, sent):
    assert P.check(raw).text == sent


REFUSED = [
    "configure", "conf", "con", "configure private", "configure exclusive", "edit", "set cli screen-length 0",
    "delete interfaces ge-0/0/1", "commit", "commit confirmed 1", "rollback 1", "load override x", "op x",
    "request system reboot", "req sys reboot", "req system zeroize", "request support information",
    "start shell", "start sh", "sta sh", "file delete /var/tmp/x", "file copy a b", "fi list",
    "clear interfaces statistics all", "clea arp", "restart routing", "res routing", "save /tmp/x", "test policy x",
    "monitor interface ge-0/0/0", "monitor traffic", "mon int ge-0/0/0", "ssh 10.0.0.1", "telnet 10.0.0.1", "help",
    "quit", "s", "pin", "show",
    # pipes
    "show version | save /tmp/x", "show version | s /tmp/x", "show version | sa /tmp/x", "show version | tee /tmp/x",
    "show version | append /tmp/x", "show version | display xml", "show version | d xml", "show version | refresh 1",
    "show version | hold", "show version | request system reboot", "show version | resolve", "show version |",
    "show version | match a b", "ping 1.1.1.1 | match x",
    # injection and characters
    "show ver; request system reboot", "show version\nrequest system reboot", "show version\r",
    "show version\tx", "show `request system reboot`", "show $(x)", "show version && x", "show version > /tmp/x",
    "show \\x", "show 'x'", 'show "x"', "show {x}",
    "ｓｈｏｗ version", "ѕhow version", "show versіon", "show version​",
    "req­sys reboot",
    # show configuration: secrets on a shared screen
    "show configuration", "show conf", "sh co system", "show configuration | display set",
    # ping / traceroute bounds and options
    "ping 10.1.0.1 count 11", "ping 10.1.0.1 count 100000", "ping 10.1.0.1 c 0", "ping 10.1.0.1 interval 0.01",
    "ping 10.1.0.1 i 0", "ping 10.1.0.1 size 65000", "ping 10.1.0.1 bypass-routing", "ping", "ping count 3",
    "ping 10.1.0.1 count 3 count 4", "ping 10.1.0.1 10.1.0.2", "traceroute 1.1.1.1 monitor",
    "traceroute 1.1.1.1 ttl 255", "traceroute 1.1.1.1 gateway 2.2.2.2", "x" * 300,
]


@pytest.mark.parametrize("raw", REFUSED)
def test_refused(raw):
    with pytest.raises(P.Refused):
        P.check(raw)


def test_every_refused_verb_and_every_prefix_of_it():
    """Every refused verb, and every prefix Junos might expand to it, is refused."""
    for verb, why in P.VERBS.items():
        if why is None:
            continue
        for n in range(1, len(verb) + 1):
            with pytest.raises(P.Refused):
                P.check(f"{verb[:n]} x")


def test_prefix_of_an_allowed_verb_only_if_unique_in_the_full_table():
    # "s" prefixes show, save, set, ssh, start, status: ambiguous, refused (never "show")
    with pytest.raises(P.Refused, match="ambiguous"):
        P.check("s version")
    assert P.check("sh version").verb == "show"


def test_output_cap():
    out, cut = P.cap_output("a" * (P.MAX_OUTPUT + 10))
    assert cut and len(out.encode()) == P.MAX_OUTPUT
    assert P.cap_output("short") == ("short", False)
