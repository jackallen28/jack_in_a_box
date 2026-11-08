"""When the Ethernet printer can't be reached, the app says why in plain words."""
import ipaddress
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import printer as pr  # noqa: E402

NET = {"backend": "network", "host": "192.168.192.168", "port": 9100}


@pytest.fixture()
def pi(monkeypatch):
    """Pretend what the Pi's network looks like: carrier + addresses."""
    state = {"carrier": True, "addrs": []}
    monkeypatch.setattr(pr, "_eth_carrier", lambda iface="eth0": state["carrier"])
    monkeypatch.setattr(pr, "_local_ipv4s", lambda: [(i, ipaddress.IPv4Interface(a)) for i, a in state["addrs"]])
    monkeypatch.setattr(pr.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("timed out")))
    return state


def failure(cfg=NET):
    ok, msg = pr.check(cfg)
    assert ok is False
    return msg


def test_no_cable_link_is_called_out(pi):
    pi.update(carrier=False, addrs=[("wlan0", "192.168.1.20/24")])
    msg = failure()
    assert "Can't reach 192.168.192.168:9100" in msg and "no link" in msg and "BOTH ends" in msg
    pi.update(addrs=[("eth0", "192.168.192.1/24"), ("wlan0", "192.168.1.20/24")])
    assert "no link" in failure()                                                               # an address but no cable: still the cable


def test_a_cable_that_is_live_but_an_ethernet_port_with_no_address_points_at_the_script(pi):
    pi.update(carrier=True, addrs=[("wlan0", "192.168.1.20/24")])
    msg = failure()
    assert "isn't on 192.168.192.168's network" in msg and "printer-ethernet.sh" in msg


def test_when_the_pi_is_set_up_but_the_printer_is_silent_it_says_to_check_the_ip(pi):
    pi.update(carrier=True, addrs=[("eth0", "192.168.192.1/24"), ("wlan0", "192.168.1.20/24")])
    msg = failure()
    assert "can see that network" in msg and "self-test sheet" in msg and "FEED" in msg


def test_a_printer_on_the_routers_network_is_judged_by_the_wifi_or_lan_address(pi):
    pi.update(carrier=None, addrs=[("wlan0", "192.168.1.20/24")])
    assert "can see that network" in failure(dict(NET, host="192.168.1.50"))
    assert "isn't on 10.0.0.5's network" in failure(dict(NET, host="10.0.0.5"))


def test_names_public_addresses_and_machines_without_a_port_get_the_generic_advice(pi):
    for host in ("printer.local", "8.8.8.8", "::1"):
        assert pr.network_hint(host) == ""
        assert "Check the cable" in failure(dict(NET, host=host))
    pi.update(carrier=None, addrs=[])
    assert "isn't on" in pr.network_hint("192.168.192.168")


def test_it_still_works_when_the_printer_answers(monkeypatch, pi):
    class Sock:
        def close(self):
            pass
    monkeypatch.setattr(pr.socket, "create_connection", lambda *a, **k: Sock())
    assert pr.check(NET) == (True, "Network printer answered at 192.168.192.168:9100.")
    assert pr.check({"backend": "network", "host": ""})[0] is False
