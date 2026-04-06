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
docker compose -f docker-compose.yml -f extensions/filebeat/filebeat-compose.yml up -d --force-recreate elasticsearch filebeat kibana
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
docker compose -f docker-compose.yml -f extensions/filebeat/filebeat-compose.yml ps
docker stats --no-stream

docker compose -f docker-compose.yml -f extensions/filebeat/filebeat-compose.yml logs --since=5m elasticsearch filebeat kibana
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
docker compose -f docker-compose.yml -f extensions/filebeat/filebeat-compose.yml up -d --force-recreate
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
