from datetime import datetime

from shimpaku.store import StateDB, Store, utcnow


def test_store_roundtrip(tmp_path):
    with Store(tmp_path / "a.duckdb") as s:
        now = utcnow()
        assert s.insert_metrics([(now, "leaf2:ge-0/0/0", "if.in_octets", 10.0),
                                 (now, "leaf2", "cpu.user", 3.0)]) == 2
        s.insert_events([{"ts": now, "ingest_ts": now, "source": "gnmi", "node": "leaf2", "entity_id": "leaf2",
                          "type": "SNMP_TRAP_LINK_DOWN", "attrs": {"interface-name": "ge-0/0/1"}, "raw": "x"}])
        s.upsert_entities([{"entity_id": "leaf2", "kind": "device", "name": "leaf2", "attrs": {"site": "fabric"}}])
        s.upsert_entities([{"entity_id": "leaf2", "kind": "device", "name": "leaf2", "attrs": {"site": "fab"}}])
        s.replace_edges([{"src": "a", "dst": "b", "relation": "connected_to"}])
        assert len(s.latest_metrics(entity_prefix="leaf2:")) == 1
        ev = s.events(node="leaf2")
        assert ev[0]["event_id"] == 1 and ev[0]["type"] == "SNMP_TRAP_LINK_DOWN"
        assert s.query("select count(*) n from entity")[0]["n"] == 1
        assert s.size_bytes() > 0
        s.insert_rows("incident", [{"incident_id": "i1", "t_open": now, "root_causes": [{"x": 1}], "status": "open"}])
        assert s.query("select status from incident")[0]["status"] == "open"


def test_state_actions_tokens_sessions(tmp_path):
    with StateDB(tmp_path / "s.db") as db:
        a = db.action_insert({"kind": "bounce_port", "args": {"device": "leaf2"}, "caller": "t"})
        assert a["status"] == "proposed" and a["args"] == {"device": "leaf2"}
        db.action_update(a["action_id"], status="approved")
        assert db.action_list(status="approved")[0]["action_id"] == a["action_id"]
        db.approval_add(a["action_id"], "rohan")
        assert db.approvals_for(a["action_id"])[0]["user"] == "rohan"
        db.token_insert("t1", "cc", "h1", ["read"])
        assert db.token_get_by_hash("h1")["scopes"] == ["read"]
        db.token_revoke("t1")
        assert db.token_get_by_hash("h1") is None
        db.session_insert("s1", "rohan", "c", ttl_s=60)
        assert db.session_get("s1")["user"] == "rohan"
        db.session_delete("s1")
        assert db.session_get("s1") is None


def test_audit_chain_detects_edit(tmp_path):
    with StateDB(tmp_path / "s.db") as db:
        for i in range(5):
            db.audit_append("agent", "tool_call", "iface_get_errors", {"i": i}, {"ok": True})
        assert db.audit_verify() == (True, None)
        db._exec("UPDATE audit SET args='{\"i\": 99}' WHERE seq=3")
        assert db.audit_verify() == (False, 3)
