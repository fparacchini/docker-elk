# Runbook operativo (versione breve) – ELK/Filebeat

## 0) Fix applicati e stato finale
- Stabilità Elasticsearch ripristinata (niente OOM ricorrenti) con heap a `1536m`.
- Cluster riportato da `red` a `green` rimuovendo il data stream bloccato `packetbeat-9.2.0`.
- Filebeat syslog corretto e validato end-to-end su TCP `:10514`.
- Kibana stabilizzato disabilitando plugin non necessari e configurando encryption keys.

### Comandi one-shot usati in produzione
```bash
# Rimozione data stream packetbeat bloccato (write index in initializing)
curl -u elastic:changeme2025 -X DELETE "http://localhost:9200/_data_stream/packetbeat-9.2.0"

# Verifica health cluster
curl -u elastic:changeme2025 "http://localhost:9200/_cluster/health?pretty"
```

## 1) Applicare configurazione
```bash
docker compose up -d --force-recreate elasticsearch filebeat kibana
```

## 2) Attendere Elasticsearch pronto
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9200
# atteso: 401 (security attiva)
```

## 3) Impostazioni API post-start (idempotenti)
```bash
# Basic license
curl -u elastic:changeme2025 -X POST "http://localhost:9200/_license/start_basic?acknowledge=true"

# Password utenti servizio
curl -u elastic:changeme2025 -X POST "http://localhost:9200/_security/user/kibana_system/_password" -H "Content-Type: application/json" -d '{"password":"changeme2025"}'
curl -u elastic:changeme2025 -X POST "http://localhost:9200/_security/user/filebeat_internal/_password" -H "Content-Type: application/json" -d '{"password":"changeme2025"}'

# Rimozione setting monitoring deprecato
curl -u elastic:changeme2025 -X PUT "http://localhost:9200/_cluster/settings" -H "Content-Type: application/json" -d '{"persistent":{"xpack.monitoring.collection.enabled":null}}'
```

## 4) Verifica rapida salute stack
```bash
docker compose ps
docker stats --no-stream

docker compose logs --since=5m elasticsearch filebeat kibana
```

## 5) Health-check automatico syslog end-to-end
Valida in un colpo solo: ingresso TCP su Filebeat (`:10514`) + indicizzazione in Elasticsearch + parse campi `log.syslog.*`.

```bash
ID="hc-syslog-$(date +%s)"; TS="$(date '+%b %e %H:%M:%S')"; \
echo "<134>${TS} hc-host hc-app[777]: ${ID} ok" | nc -w1 127.0.0.1 10514; \
sleep 4; \
curl -s -u elastic:changeme2025 -H 'Content-Type: application/json' \
'http://localhost:9200/filebeat-*/_search' -d "{\"size\":1,\"sort\":[{\"@timestamp\":{\"order\":\"desc\"}}],\"query\":{\"match\":{\"message\":\"${ID}\"}},\"_source\":[\"@timestamp\",\"message\",\"input.type\",\"tags\",\"log.syslog.hostname\",\"log.syslog.appname\",\"log.syslog.procid\"]}" | jq .
```

### Esito atteso
- `hits.total.value >= 1`
- `_source.input.type = tcp`
- `_source.log.syslog.hostname/appname/procid` valorizzati
- `_source.tags` contiene `syslog-ng` e `rfc3164`

## 6) Criteri di OK
- Elasticsearch risponde `401` su `:9200`
- Filebeat log contiene: `Connection ... established`
- Assenza di errori ricorrenti: `OutOfMemoryError`, `circuit_breaking_exception`, `401 Unauthorized` su Filebeat
- Cluster health: `green`

## 7) Rollback veloce (se necessario)
1. Ripristinare i file configurazione da backup/version control.
2. Riavviare servizi:
```bash
docker compose up -d --force-recreate
```

## 8) Parametri finali applicati (promemoria)
- ES heap: `-Xms1536m -Xmx1536m`
- Breaker: total `95%`, request `50%`, fielddata `20%`
- Filebeat: `bulk_max_size: 500`, `worker: 1`
- Filebeat syslog input: `type: tcp` su `0.0.0.0:10514`
- Filebeat syslog processor: `field: message`, `format: rfc3164`
- Filebeat metadata pipeline forzata: `@metadata.pipeline = syslog`
- Template: `1 shard`, `0 repliche`
- ILM: rollover `1d/500mb`, delete `7d`
- Kibana: encryption keys impostate, telemetry disabilitata
- Kibana plugin disabilitati per ridurre errori/overhead: `xpack.fleet.enabled: false`, `xpack.securitySolution.enabled: false`
- Elasticsearch: `xpack.security.transport.ssl.enabled: false` (single-node)

## 9) Configurazione ottimizzata (estratto)

### Elasticsearch (`docker-compose.yml` + `elasticsearch.yml`)
- `ES_JAVA_OPTS: -Xms1536m -Xmx1536m -XX:+UseG1GC -XX:MaxGCPauseMillis=50 -XX:InitiatingHeapOccupancyPercent=25`
- `indices.fielddata.cache.size: 100mb`
- `indices.memory.index_buffer_size: 5%`
- `indices.breaker.total.limit: 95%`
- `indices.breaker.request.limit: 50%`
- `indices.breaker.fielddata.limit: 20%`

### Filebeat (`extensions/filebeat/config/filebeat.yml`)
- Input syslog:
	- `type: tcp`
	- `host: 0.0.0.0:10514`
	- `pipeline: syslog`
	- `processors.syslog.field: message`
	- `processors.syslog.format: rfc3164`
	- `@metadata.pipeline: syslog`
- Output:
	- `bulk_max_size: 500`
	- `worker: 1`
- Data management:
	- template `1 shard / 0 repliche`
	- ILM rollover `1d/500mb`, delete `7d`

### Kibana (`kibana/config/kibana.yml`)
- `xpack.security.encryptionKey`, `xpack.encryptedSavedObjects.encryptionKey`, `xpack.reporting.encryptionKey` impostate
- `telemetry.enabled: false`
- `xpack.fleet.enabled: false`
- `xpack.securitySolution.enabled: false`

---

## 10) DHCP Enrichment Pipeline – OpenWRT (2026-04-06)

Arricchimento automatico degli eventi `dawn` e `hostapd` con l'IP e l'hostname assegnati da `dnsmasq-dhcp`, tramite lookup MAC Address → DHCP lease.

### Architettura

```
dnsmasq-dhcp DHCPACK  →  pipeline syslog  →  filebeat-*
                                                   ↓
                                     dhcp-leases-refresh.py (cron */5)
                                                   ↓
                                         dhcp-leases-current (1 doc/MAC)
                                                   ↓
                                       dhcp-leases-policy (enrich snapshot)
                                                   ↓
dawn/hostapd event  →  pipeline syslog  →  enrich processor
                              ↓
               source.ip + source.domain aggiunti al documento
```

### Bug corretti in `pipeline-syslog.json`

| Tag processor | Problema | Fix |
|---|---|---|
| `dhcp_line` | `%{?_dhcp.ip}` e `%{?_dhcp.hostname}` — il modificatore `?` scartava i valori; `source.ip` e `source.domain` non venivano mai impostati | Rimosso `?` da entrambi i campi |
| `dnsmasq_dhcp_strip_txid` | I messaggi `dnsmasq-dhcp` avevano un transaction-ID numerico prefisso (`3277264775 DHCPACK…`) che veniva incluso in `event.action` | Aggiunto `gsub` che rimuove `^[0-9]+\s` |
| `hostapd_main` | I pattern grok richiedevano il prefisso `interface:` (`wlan0: AP-STA-…`), ma i messaggi reali post-forwarding ne sono privi | Aggiunti pattern alternativi senza prefisso interfaccia |
| `dhcp_line_no_hostname` | Alcuni messaggi DHCP non includono il campo hostname (es. device senza opzione DHCP 12) | Aggiunto dissect di fallback senza il campo hostname |

### Nuovi processor aggiunti (blocco `DHCP ENRICH`)

Posizionati dopo i blocchi `HOSTAPD` e prima di `CLEANUP`:

- `dhcp_enrich_lookup` — `enrich` processor: lookup `source.mac` → policy `dhcp-leases-policy`, risultato in `_tmp.dhcp_enrich`
- `dhcp_enrich_ip` — `set`: copia `_tmp.dhcp_enrich.source.ip` → `source.ip` (solo se non già presente)
- `dhcp_enrich_domain` — `set`: copia `_tmp.dhcp_enrich.source.domain` → `source.domain` (solo se non già presente)

### Risorse Elasticsearch create

```bash
# Index template (mapping esplicito mac=keyword, ip=ip)
PUT /_index_template/dhcp-leases-current-template

# Enrich policy
PUT /_enrich/policy/dhcp-leases-policy
# { "match": { "indices": "dhcp-leases-current", "match_field": "source.mac",
#   "enrich_fields": ["source.ip", "source.domain"] } }

# Prima esecuzione (necessaria dopo ogni modifica alla policy)
POST /_enrich/policy/dhcp-leases-policy/_execute
```

> **Nota**: il nodo non ha il ruolo `transform` (`xpack.ml.enabled: false` + roles espliciti in `elasticsearch.yml`). Il Continuous Transform è sostituito dallo script cron.

### Script di refresh: `setup/dhcp-leases-refresh.py`

Eseguito ogni 5 minuti via cron. Operazioni:
1. Scroll di tutti i messaggi `DHCPACK` da `filebeat-*` (ordinati per `@timestamp asc` → tiene il più recente per MAC)
2. Bulk upsert su `dhcp-leases-current` (doc-id = MAC con `:` → `-`)
3. `POST /_enrich/policy/dhcp-leases-policy/_execute`

```bash
# Cron attivo (utente fparacchini)
*/5 * * * * /usr/bin/python3 /opt/docker-elk/setup/dhcp-leases-refresh.py >> /var/log/dhcp-leases-refresh.log 2>&1

# Esecuzione manuale
python3 /opt/docker-elk/setup/dhcp-leases-refresh.py

# Log
tail -f /var/log/dhcp-leases-refresh.log
```

### Deploy pipeline in produzione

```bash
cd /opt/docker-elk
python3 - <<'EOF'
import re, json, urllib.request, base64
with open('pipeline-syslog.json') as f:
    raw = f.read()
cleaned = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)
payload = json.dumps(json.loads(cleaned)).encode()
req = urllib.request.Request('http://localhost:9200/_ingest/pipeline/syslog', data=payload, method='PUT')
req.add_header('Content-Type', 'application/json')
req.add_header('Authorization', 'Basic ' + base64.b64encode(b'elastic:<PASSWORD>').decode())
with urllib.request.urlopen(req) as r:
    print(r.status, r.read().decode())
EOF
```

### Verifica end-to-end (simulate)

```bash
curl -s -u elastic:<PASSWORD> 'http://localhost:9200/_ingest/pipeline/syslog/_simulate' \
  -H 'Content-Type: application/json' \
  -d '{
  "docs": [{
    "_source": {
      "message": "AP-STA-CONNECTED 46:aa:0e:f7:61:0e auth_alg=sae",
      "log": { "syslog": { "appname": "hostapd", "hostname": "AP-1" } },
      "host": { "hostname": "AP-1" }
    }
  }]
}' | python3 -c "
import sys,json
d=json.load(sys.stdin)['docs'][0]['doc']['_source']
print('source.mac   :', d.get('source',{}).get('mac'))
print('source.ip    :', d.get('source',{}).get('ip'))
print('source.domain:', d.get('source',{}).get('domain'))
print('event.action :', d.get('event',{}).get('action'))
"
# Atteso: source.ip e source.domain valorizzati per MAC noto
```

### Dashboard Kibana

Dashboard "DHCP & WiFi Events – OpenWRT":
- URL: `http://localhost:5601/app/dashboards#/view/dash-dhcp-wifi-openwrt`
- Script di (ri)creazione: `setup/kibana-dashboard-create.py` (idempotente, `?overwrite=true`)

```bash
python3 /opt/docker-elk/setup/kibana-dashboard-create.py
```

| Panel | Tipo | Sorgente | Contenuto |
|---|---|---|---|
| DHCP Leases – IP Assegnati | Lens Datatable | `dhcp-leases-current` | MAC, IP, Hostname, Ultimo DHCPACK |
| WiFi Events Nel Tempo | Lens XY bar stacked | `filebeat-*` | Conteggio eventi per app nel tempo |
| WiFi Events Log | Discover | `filebeat-*` | Log dettagliato con appname, mac, ip, domain, action, host |

Cliccare su MAC o IP nel primo panel aggiunge un filtro globale che filtra automaticamente gli altri due panel.
