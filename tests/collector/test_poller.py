from pathlib import Path

from shimpaku.collector.poller import Poller, parse_nexthops, parse_rpm_xml, rpm_rows
from shimpaku.store import Store

FIX = Path(__file__).parent / "fixtures"


def test_rpm_real_xml():
    rows = rpm_rows("sw1", (FIX / "rpm_sw1.xml").read_text())
    assert rows and all(r["client_id"].startswith("rpm:sw1:shimpaku/") for r in rows)
    r = rows[0]
    assert r["kind"] == "rpm-icmp" and r["loss_pct"] is not None and r["rtt_ms"] and r["rtt_ms"] < 1000
    assert parse_rpm_xml("sw1", (FIX / "rpm_sw1.xml").read_text())[0].keys() == r.keys()


def test_nexthops():
    assert parse_nexthops((FIX / "route_leaf2.xml").read_text()) == {"10.255.0.41/32": 2}


class FakePool:
    class Dev:
        class rpc:
            @staticmethod
            def get_probe_results(owner):
                from lxml import etree
                return etree.fromstring((FIX / "rpm_sw1.xml").read_bytes())

            @staticmethod
            def get_route_information(destination):
                from lxml import etree
                return etree.fromstring((FIX / "route_leaf2.xml").read_bytes())

    def run(self, name, fn):
        return fn(self.Dev)


def test_poll_node_writes_rows():
    with Store(":memory:") as s:
        p = Poller(s, rpm_nodes=["leaf2"], ecmp={"leaf2": ["10.255.0.41"]}, pool=FakePool())
        probes, metrics = p.poll_node("leaf2")
        s.insert_rows("probe_result", probes)
        s.insert_metrics(metrics)
        assert s.query("select count(*) n from probe_result")[0]["n"] == len(probes) > 0
        assert metrics[0][1:] == ("leaf2:route:10.255.0.41/32", "ecmp.nexthops", 2.0)
