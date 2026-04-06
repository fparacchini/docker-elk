#!/usr/bin/env python3
"""
dhcp-leases-refresh.py
Aggiorna dhcp-leases-current con i DHCPACK più recenti da filebeat-*
e ri-esegue la Enrich Policy dhcp-leases-policy.

Eseguire come cron ogni 5 minuti:
  */5 * * * * /usr/bin/python3 /opt/docker-elk/setup/dhcp-leases-refresh.py >> /var/log/dhcp-leases-refresh.log 2>&1
"""

import re, json, urllib.request, urllib.error, base64, sys
from datetime import datetime, timezone

ES_HOST  = "http://localhost:9200"
ES_USER  = "elastic"
ES_PASS  = "changeme2025"
DEST_IDX = "dhcp-leases-current"
POLICY   = "dhcp-leases-policy"

AUTH = base64.b64encode(f"{ES_USER}:{ES_PASS}".encode()).decode()
HEADERS = {"Content-Type": "application/json", "Authorization": f"Basic {AUTH}"}

# Regex: optional txid prefix, then DHCPACK(iface) IP MAC optional_hostname
PAT = re.compile(
    r'(?:[0-9]+\s)?DHCPACK\([^)]+\)\s+'
    r'(?P<ip>\d+\.\d+\.\d+\.\d+)\s+'
    r'(?P<mac>[0-9a-fA-F:]{17})\s*'
    r'(?P<hostname>\S+)?'
)


def es_request(method, path, body=None, content_type="application/json"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{ES_HOST}{path}", data=data, method=method)
    req.add_header("Content-Type", content_type)
    req.add_header("Authorization", f"Basic {AUTH}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def bulk_ndjson(lines):
    body = "\n".join(lines) + "\n"
    req = urllib.request.Request(f"{ES_HOST}/_bulk", data=body.encode(), method="POST")
    req.add_header("Content-Type", "application/x-ndjson")
    req.add_header("Authorization", f"Basic {AUTH}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    ts_now = datetime.now(timezone.utc).isoformat()

    # --- 1. Query DHCPACK events (new ones have source.ip; old ones parsed here) ---
    leases = {}  # mac -> doc
    after = None

    while True:
        query = {
            "size": 1000,
            "_source": ["message", "source.mac", "source.ip", "source.domain", "@timestamp"],
            "query": {"bool": {"must": [
                {"term": {"log.syslog.appname": "dnsmasq-dhcp"}},
                {"match_phrase": {"message": "DHCPACK"}}
            ]}},
            "sort": [{"@timestamp": "asc"}]
        }
        if after:
            query["search_after"] = after

        _, result = es_request("POST", "/.ds-filebeat-*/_search", query)
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            break

        for h in hits:
            src = h["_source"]
            mac = src.get("source", {}).get("mac")
            ip  = src.get("source", {}).get("ip")
            hn  = src.get("source", {}).get("domain")
            evt_ts = src.get("@timestamp", "")

            # Fallback: parse from message (old events before bug fix)
            if not ip:
                m = PAT.search(src.get("message", ""))
                if m:
                    mac = m.group("mac").lower()
                    ip  = m.group("ip")
                    hn  = m.group("hostname") or None

            if mac and ip:
                leases[mac.lower()] = {
                    "source": {"mac": mac.lower(), "ip": ip, **({"domain": hn} if hn else {})},
                    "@timestamp": evt_ts
                }

        after = hits[-1].get("sort")
        if len(hits) < 1000:
            break

    if not leases:
        print(f"[{ts_now}] No DHCPACK events found. Nothing to update.")
        return

    # --- 2. Bulk upsert into dhcp-leases-current ---
    lines = []
    for mac, doc in leases.items():
        doc_id = mac.replace(":", "-")
        lines.append(json.dumps({"index": {"_index": DEST_IDX, "_id": doc_id}}))
        lines.append(json.dumps(doc))

    resp = bulk_ndjson(lines)
    errors = resp.get("errors", False)
    ok = sum(1 for i in resp.get("items", []) if i.get("index", {}).get("result") in ("created", "updated"))
    print(f"[{ts_now}] Bulk upsert: {ok}/{len(leases)} MACs, errors={errors}")

    if errors:
        for item in resp.get("items", []):
            if item.get("index", {}).get("error"):
                print(f"  ERROR: {item['index']['error']}", file=sys.stderr)

    # --- 3. Re-execute enrich policy ---
    status, result = es_request("POST", f"/_enrich/policy/{POLICY}/_execute")
    phase = result.get("status", {}).get("phase", "?")
    print(f"[{ts_now}] Enrich policy execute: HTTP {status}, phase={phase}")


if __name__ == "__main__":
    main()
