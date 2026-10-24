-- Shimpaku agent-visible database (DuckDB, aiops.duckdb). From R5 07_schema.sql with these changes:
--   * user_minute is named probe_minute (CONTRACT.md);
--   * action lives in SQLite state.db, not here (CONTRACT.md);
--   * event gains ingest_ts (host arrival) and node; event_id defaults from a sequence;
--   * entity_edge gains attrs (interface names, subnet, evidence).
-- Ground truth never enters this database.
CREATE SEQUENCE IF NOT EXISTS event_seq START 1;
CREATE TABLE IF NOT EXISTS entity (entity_id VARCHAR PRIMARY KEY, kind VARCHAR, name VARCHAR, parent_id VARCHAR, attrs JSON);
CREATE TABLE IF NOT EXISTS entity_edge (src VARCHAR, dst VARCHAR, relation VARCHAR, attrs JSON);  -- connected_to / member_of / depends_on
CREATE TABLE IF NOT EXISTS metric_sample (ts TIMESTAMP, entity_id VARCHAR, metric VARCHAR, value DOUBLE);  -- 10 s raw, UTC
CREATE TABLE IF NOT EXISTS event (event_id BIGINT DEFAULT nextval('event_seq'), ts TIMESTAMP, ingest_ts TIMESTAMP,
    source VARCHAR, node VARCHAR, entity_id VARCHAR, client_id VARCHAR, type VARCHAR, attrs JSON, raw VARCHAR);
CREATE TABLE IF NOT EXISTS connect_attempt (attempt_id BIGINT, client_id VARCHAR, access VARCHAR, t_start TIMESTAMP,
    d_assoc DOUBLE, d_auth DOUBLE, d_dhcp DOUBLE, d_arp DOUBLE, d_dns DOUBLE, ttc DOUBLE, outcome VARCHAR,
    fail_stage VARCHAR, fail_sub VARCHAR, leaf VARCHAR, port VARCHAR, ap VARCHAR, ssid VARCHAR, vlan VARCHAR,
    auth VARCHAR, radius VARCHAR, dhcp_server VARCHAR, dns_server VARCHAR, os VARCHAR, pcap_id VARCHAR);
CREATE TABLE IF NOT EXISTS probe_result (ts TIMESTAMP, client_id VARCHAR, target VARCHAR, kind VARCHAR, ok BOOLEAN,
    rtt_ms DOUBLE, jitter_ms DOUBLE, loss_pct DOUBLE, dns_ms DOUBLE, connect_ms DOUBLE, ttfb_ms DOUBLE, mos DOUBLE);
CREATE TABLE IF NOT EXISTS probe_minute (minute TIMESTAMP, client_id VARCHAR, sle VARCHAR, status VARCHAR,
    classifier VARCHAR, sub_classifier VARCHAR, value DOUBLE, threshold_version VARCHAR,
    leaf VARCHAR, ap VARCHAR, ssid VARCHAR, vlan VARCHAR, access VARCHAR, evidence JSON);
CREATE TABLE IF NOT EXISTS device_minute (minute TIMESTAMP, entity_id VARCHAR, sle VARCHAR, status VARCHAR,
    classifier VARCHAR, sub_classifier VARCHAR, evidence JSON);
CREATE TABLE IF NOT EXISTS anomaly (anomaly_id VARCHAR, series VARCHAR, method VARCHAR, t_start TIMESTAMP, t_end TIMESTAMP,
    score DOUBLE, expected DOUBLE, observed DOUBLE);
CREATE TABLE IF NOT EXISTS incident (incident_id VARCHAR, t_open TIMESTAMP, t_detect TIMESTAMP, t_close TIMESTAMP, sle VARCHAR,
    root_causes JSON, blast_radius JSON, explanation VARCHAR, status VARCHAR);
