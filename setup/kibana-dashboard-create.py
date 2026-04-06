#!/usr/bin/env python3
"""
Crea la Kibana dashboard "DHCP & WiFi Events – OpenWRT":
  Panel 1: Tabella DHCP Leases (MAC → IP, hostname, last seen)
  Panel 2: Timeline eventi hostapd/dawn/dnsmasq-dhcp per app
  Panel 3: Log eventi dettagliato (Discover)
Cliccare su un MAC o IP nel panel 1 filtra i panel 2 e 3.
"""
import json, urllib.request, urllib.error, base64, sys

KB   = "http://localhost:5601"
AUTH = base64.b64encode(b"elastic:changeme2025").decode()

DV_LEASES  = "dv-dhcp-leases-0001"
DV_FB      = "dv-filebeat-syslog-0001"
LENS_TBL   = "lens-dhcp-leases-tbl"
LENS_TIME  = "lens-wifi-timeline"
SEARCH_LOG = "search-wifi-events"
DASH_ID    = "dash-dhcp-wifi-openwrt"


def kb(method, path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{KB}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {AUTH}")
    req.add_header("kbn-xsrf", "true")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw.decode()[:2000]}


def create_obj(type_, id_, attrs, refs):
    """Create or overwrite a Kibana saved object via POST."""
    s, r = kb("POST", f"/api/saved_objects/{type_}/{id_}?overwrite=true",
              {"attributes": attrs, "references": refs})
    ok = s in (200, 201)
    label = f"  {'OK' if ok else 'FAIL'} [{type_}/{id_}]"
    if ok:
        print(label)
    else:
        print(f"{label} → HTTP {s}: {json.dumps(r)[:300]}")
    return ok


# ── Check Kibana ──────────────────────────────────────────────────────────
s, r = kb("GET", "/api/status", {})
if s != 200:
    print(f"Kibana non raggiungibile: {s}"); sys.exit(1)
kb_ver = r.get("version", {}).get("number", "9.2.2")
print(f"Kibana {kb_ver}\n")


# ① Data view: dhcp-leases-current
create_obj("index-pattern", DV_LEASES, {
    "title": "dhcp-leases-current",
    "name": "DHCP Leases",
    "timeFieldName": "@timestamp",
    "fields": "[]", "fieldAttrs": "{}", "runtimeFieldMap": "{}",
    "sourceFilters": "[]", "typeMeta": "{}"
}, [])


# ② Data view: filebeat-*
create_obj("index-pattern", DV_FB, {
    "title": "filebeat-*",
    "name": "Filebeat Syslog",
    "timeFieldName": "@timestamp",
    "fields": "[]", "fieldAttrs": "{}", "runtimeFieldMap": "{}",
    "sourceFilters": "[]", "typeMeta": "{}"
}, [])


# ③ Lens: DHCP Leases Table (source: dhcp-leases-current)
create_obj("lens", LENS_TBL, {
    "title": "DHCP Leases – IP Assegnati",
    "description": "IP, hostname e MAC degli host con lease DHCP attivo. Clicca su una riga per filtrare gli altri panel.",
    "visualizationType": "lnsDatatable",
    "state": {
        "datasourceStates": {
            "formBased": {
                "layers": {
                    "l1": {
                        "columnOrder": ["c-mac", "c-ip", "c-domain", "c-ts"],
                        "columns": {
                            "c-mac": {
                                "label": "MAC Address", "dataType": "string",
                                "operationType": "terms", "sourceField": "source.mac",
                                "isBucketed": True, "scale": "ordinal",
                                "params": {
                                    "size": 100,
                                    "orderBy": {"type": "alphabetical"},
                                    "orderDirection": "asc",
                                    "otherBucket": False, "missingBucket": False
                                }
                            },
                            "c-ip": {
                                "label": "IP Assegnato", "dataType": "ip",
                                "operationType": "last_value", "sourceField": "source.ip",
                                "isBucketed": False, "scale": "ratio",
                                "params": {"sortField": "@timestamp", "showArrayValues": False}
                            },
                            "c-domain": {
                                "label": "Hostname", "dataType": "string",
                                "operationType": "last_value", "sourceField": "source.domain",
                                "isBucketed": False, "scale": "ordinal",
                                "params": {"sortField": "@timestamp", "showArrayValues": False}
                            },
                            "c-ts": {
                                "label": "Ultimo DHCPACK", "dataType": "date",
                                "operationType": "last_value", "sourceField": "@timestamp",
                                "isBucketed": False, "scale": "interval",
                                "params": {"sortField": "@timestamp", "showArrayValues": False}
                            }
                        },
                        "incompleteColumns": {},
                        "indexPatternId": DV_LEASES
                    }
                }
            }
        },
        "visualization": {
            "layerId": "l1", "layerType": "data",
            "columns": [
                {"columnId": "c-mac", "isTransposed": False},
                {"columnId": "c-ip",  "isTransposed": False},
                {"columnId": "c-domain", "isTransposed": False},
                {"columnId": "c-ts", "isTransposed": False}
            ]
        },
        "query": {"query": "", "language": "kuery"},
        "filters": [], "internalReferences": [], "adHocDataViews": {}
    }
}, [{"id": DV_LEASES, "name": "indexpattern-datasource-layer-l1", "type": "index-pattern"}])


# ④ Lens: Events Timeline (source: filebeat-*)
create_obj("lens", LENS_TIME, {
    "title": "WiFi Events Nel Tempo",
    "description": "Istogramma hostapd/dawn/dnsmasq-dhcp. Filtrato automaticamente dal MAC/IP selezionato.",
    "visualizationType": "lnsXY",
    "state": {
        "datasourceStates": {
            "formBased": {
                "layers": {
                    "l2": {
                        "columnOrder": ["c-date", "c-app", "c-count"],
                        "columns": {
                            "c-date": {
                                "label": "@timestamp", "dataType": "date",
                                "operationType": "date_histogram", "sourceField": "@timestamp",
                                "isBucketed": True, "scale": "interval",
                                "params": {"interval": "auto", "includeEmptyRows": True}
                            },
                            "c-app": {
                                "label": "App", "dataType": "string",
                                "operationType": "terms", "sourceField": "log.syslog.appname",
                                "isBucketed": True, "scale": "ordinal",
                                "params": {
                                    "size": 5,
                                    "orderBy": {"type": "column", "columnId": "c-count"},
                                    "orderDirection": "desc",
                                    "otherBucket": False, "missingBucket": False
                                }
                            },
                            "c-count": {
                                "label": "Events", "dataType": "number",
                                "operationType": "count", "isBucketed": False,
                                "scale": "ratio", "sourceField": "___records___"
                            }
                        },
                        "incompleteColumns": {},
                        "indexPatternId": DV_FB
                    }
                }
            }
        },
        "visualization": {
            "layers": [{
                "layerId": "l2", "layerType": "data",
                "xAccessor": "c-date", "accessors": ["c-count"],
                "splitAccessor": "c-app", "seriesType": "bar_stacked"
            }],
            "legend": {"isVisible": True, "position": "right", "legendSize": "auto"},
            "valueLabels": "hide",
            "fittingFunction": "None",
            "axisTitlesVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
            "tickLabelsVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
            "gridlinesVisibilitySettings": {"x": True, "yLeft": True, "yRight": True},
            "preferredSeriesType": "bar_stacked"
        },
        "query": {
            "query": "log.syslog.appname: (dawn OR hostapd OR dnsmasq-dhcp)",
            "language": "kuery"
        },
        "filters": [], "internalReferences": [], "adHocDataViews": {}
    }
}, [{"id": DV_FB, "name": "indexpattern-datasource-layer-l2", "type": "index-pattern"}])


# ⑤ Discover saved search: Events Log
ss_json = json.dumps({
    "highlightAll": True,
    "version": True,
    "query": {
        "query": "log.syslog.appname: (dawn OR hostapd OR dnsmasq-dhcp)",
        "language": "kuery"
    },
    "filter": [],
    "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"
})
create_obj("search", SEARCH_LOG, {
    "title": "WiFi Events Log – dawn · hostapd · dnsmasq",
    "description": "",
    "columns": ["log.syslog.appname", "source.mac", "source.ip", "source.domain",
                "event.action", "host.hostname"],
    "sort": [["@timestamp", "desc"]],
    "kibanaSavedObjectMeta": {"searchSourceJSON": ss_json}
}, [{"id": DV_FB, "name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern"}])


# ⑥ Dashboard
#   Grid 48 cols wide:
#   y=0  h=16: [DHCP Leases 0..23] | [Timeline 24..47]
#   y=16 h=22: [Events Log full width]
panels = json.dumps([
    {
        "version": kb_ver, "type": "lens",
        "gridData": {"x": 0,  "y": 0,  "w": 24, "h": 16, "i": "p0"},
        "panelIndex": "p0",
        "embeddableConfig": {"enhancements": {}},
        "panelRefName": "panel-0"
    },
    {
        "version": kb_ver, "type": "lens",
        "gridData": {"x": 24, "y": 0,  "w": 24, "h": 16, "i": "p1"},
        "panelIndex": "p1",
        "embeddableConfig": {"enhancements": {}},
        "panelRefName": "panel-1"
    },
    {
        "version": kb_ver, "type": "search",
        "gridData": {"x": 0,  "y": 16, "w": 48, "h": 22, "i": "p2"},
        "panelIndex": "p2",
        "embeddableConfig": {"enhancements": {}},
        "panelRefName": "panel-2"
    }
])
create_obj("dashboard", DASH_ID, {
    "title": "DHCP & WiFi Events – OpenWRT",
    "description": (
        "Lease DHCP attivi (IP, hostname, MAC) + eventi hostapd/dawn/dnsmasq nel tempo. "
        "Clicca su un MAC o IP nel pannello leases per filtrare timeline e log."
    ),
    "panelsJSON": panels,
    "optionsJSON": json.dumps({
        "hidePanelTitles": False,
        "useMargins": True,
        "syncColors": True,
        "syncCursor": True,
        "syncTooltips": False
    }),
    "timeRestore": False,
    "kibanaSavedObjectMeta": {
        "searchSourceJSON": json.dumps({
            "query": {"query": "", "language": "kuery"},
            "filter": []
        })
    }
}, [
    {"id": LENS_TBL,   "name": "panel-0", "type": "lens"},
    {"id": LENS_TIME,  "name": "panel-1", "type": "lens"},
    {"id": SEARCH_LOG, "name": "panel-2", "type": "search"}
])

print(f"\nDashboard: {KB}/app/dashboards#/view/{DASH_ID}")
