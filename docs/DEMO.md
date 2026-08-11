# Minimal demo (agent-only, not the full stack)

**Ya, bisa.** [docs/RUNBOOK.md](RUNBOOK.md) mengasumsikan seluruh `make up` (~20 container) sudah
jalan. File ini sebaliknya: cara menyalakan **hanya** yang benar-benar dibutuhkan agent
(`agent-harness`) untuk merespons, dan sengaja **tidak** menyalakan `langfuse`+turunannya (paling
berat & paling sering crash-loop di laptop ini — lihat CLAUDE.md), `rabbitmq`/`async-worker` (cuma
untuk jalur async), `services/ingestion` (cuma untuk re-ingest, bukan untuk query), atau
`services/eval` (bukan container sama sekali).

Kuncinya: `docker compose up -d <servicename>` **tetap** menyalakan semua `depends_on` service itu
(termasuk `langfuse`, lihat CLAUDE.md's known-quirks) kecuali dipanggil dengan `--no-deps`. Dengan
`--no-deps`, Compose **tidak** menyalakan apa pun secara otomatis — jadi setiap dependensi yang
benar-benar dipakai saat runtime harus ditulis eksplisit di command-nya. Itu yang dilakukan file ini.

**Kedua profil di bawah sudah diuji live** dari kondisi bersih (`make down` dulu, lalu jalankan
persis command di bawah tanpa modifikasi) — Profil A: 6 container, `HTTP 200`,
`"degraded":["retrieval"]`, jawaban benar dari tool call nyata. Profil B: 10 container di atas
Profil A (+`rabbitmq`, `mock-idp`, `agent-gateway`, `kong`), login asli lewat `mock-idp`, request
lewat Kong, hasil sama. Satu bug ketemu & sudah diperbaiki dalam prosesnya: contoh body
`AgentRunRequest` awal lupa field wajib `budget` (`TokenBudget`) — sudah dibetulkan di kedua contoh
di bawah dan di RUNBOOK.md §3.8.

## Dua profil

| Profil | Container | Lewat Kong/JWT? | Cocok untuk |
|---|---|---|---|
| **A — Langsung ke harness** | 6: `postgres`, `redis`, `model-router`, `ollama`, `mock-business-api`, `agent-harness` | Tidak — `/internal/v1/runs` tidak cek auth sendiri (itu tugas gateway, §8.4) | Tes tercepat/teringan, cukup untuk lihat guardrails+tool-calling+respons LLM |
| **B — Lewat Gateway + Kong** | 10: profil A + `rabbitmq`, `mock-idp`, `agent-gateway`, `kong` | Ya, login asli via `mock-idp` | Demo yang mau menunjukkan jalur publik sungguhan (rate-limit, idempotency, quota) |

Keduanya **tidak** menyalakan `retrieval-service`/`infinity` — RAG akan otomatis *degraded*
(`"degraded": ["retrieval"]`, tanpa sitasi), bukan gagal. Ini bukan asumsi, tapi perilaku kode yang
eksplisit — lihat bagian "Kenapa aman di-skip" di bawah untuk buktinya di baris kode.

## Setup sekali di awal

```bash
cd agent-platform-reference
cp .env.example .env   # kalau belum ada
# fungsi bantu, tempel sekali per sesi terminal
wait_healthy() {
  local i=0
  until [ -z "$(docker compose -f deploy/docker-compose.yml --env-file .env ps $1 --format '{{.Health}}' 2>/dev/null | grep -vE '^healthy$|^$')" ]; do
    i=$((i+1))
    if [ $i -ge 60 ]; then echo "timeout menunggu: $1"; docker compose -f deploy/docker-compose.yml --env-file .env ps $1; return 1; fi
    sleep 2
  done
}
```

## Profil A — Langsung ke harness (paling ringan)

```bash
# fase 1: infra dasar (tidak saling bergantung)
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps \
  postgres redis ollama mock-business-api
wait_healthy "postgres redis ollama mock-business-api"

# fase 2: model-router butuh postgres+ollama sehat lebih dulu
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps model-router
wait_healthy "model-router"

# fase 3: harness butuh semuanya di atas
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps agent-harness
wait_healthy "agent-harness"
```

`/internal/v1/runs` tidak verifikasi JWT (itu tugas gateway) — jadi cukup isi field `AgentRunRequest`
langsung, tanpa login:

```bash
curl -s -X POST http://localhost:8081/internal/v1/runs -H 'Content-Type: application/json' -d "{
  \"run_id\": \"run_$(uuidgen)\", \"trace_id\": \"trc_$(uuidgen)\",
  \"tenant_id\": \"tnt_demo\", \"user_id\": \"usr_budi\", \"employee_id\": \"emp_001\",
  \"acl_group_ids\": [\"grp_all_staff\", \"grp_engineering\"],
  \"permissions\": [\"policy.read\", \"leave.balance.read\", \"leave.request.create\", \"payslip.read\"],
  \"roles\": [\"employee\"], \"scope_context\": {},
  \"agent_id\": \"hr-assistant\", \"input\": {\"type\": \"text\", \"content\": \"Sisa cuti saya berapa hari?\"},
  \"context\": {}, \"options\": {}, \"execution_mode\": \"sync\",
  \"budget\": {\"pool\": \"sync\", \"reserved_tokens\": 4000}
}" | python3 -m json.tool
```

Contoh respons asli (sudah diuji live, bukan teori): `HTTP 200`,
`"output":{"content":"Anda memiliki 8 hari cuti."}`, `"degraded":["retrieval"]` (retrieval-service
memang tidak dijalankan — tetap 200, cuma tanpa sitasi, persis seperti dijelaskan di bawah).

Ganti `content` untuk skenario lain (guardrails, mutasi — tinggal tambah
`"options": {"allow_mutations": true}`). Untuk skenario approval, harness masih butuh cara membuat
keputusan approval — endpoint itu ada di **gateway** (`POST /v1/approvals/{id}/decision`), jadi
approval end-to-end tetap butuh Profil B.

## Profil B — Lewat Gateway + Kong

```bash
# fase 1: infra dasar
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps \
  postgres redis rabbitmq ollama mock-idp mock-business-api
wait_healthy "postgres redis rabbitmq ollama mock-idp mock-business-api"

# fase 2
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps model-router
wait_healthy "model-router"

# fase 3: harness
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps agent-harness
wait_healthy "agent-harness"

# fase 4: gateway lalu kong (urutan penting — kong depends_on gateway)
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps agent-gateway
wait_healthy "agent-gateway"
docker compose -f deploy/docker-compose.yml --env-file .env up -d --no-deps kong
wait_healthy "kong"
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8087/oauth/token -H 'Content-Type: application/json' \
  -d '{"user_id": "usr_budi"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://localhost:8000/v1/agent/invoke \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: $(uuidgen)" -H 'Content-Type: application/json' \
  -d '{"agent_id": "hr-assistant", "input": {"type": "text", "content": "Sisa cuti saya berapa hari?"}}' \
  | python3 -m json.tool
```

Dari sini semua skenario di [RUNBOOK.md §3](RUNBOOK.md#3-scenarios) yang tidak butuh RAG/async/eval
bisa langsung dipakai (guardrails, mutasi+approval, RBAC, killswitch) — cuma ganti port dasarnya
tetap `localhost:8000` seperti biasa.

## Kenapa aman di-skip

| Yang di-skip | Buktinya tidak bikin gagal, cuma degraded |
|---|---|
| `retrieval-service` / `infinity` (RAG) | `retrieve` node membungkus panggilannya dengan `try/except httpx.HTTPError: return {"degraded": ["retrieval"]}` — [graph/build.py:231-237](../services/harness/src/harness/graph/build.py#L231-L237). Request tetap 200, cuma tanpa sitasi. |
| `infinity` (semantic cache) | `cache_lookup` node punya `except httpx.HTTPError` yang sama — [graph/build.py:189-196](../services/harness/src/harness/graph/build.py#L189-L196) — gagal embed = `degraded: ["semantic_cache"]`, bukan gagal total. |
| `langfuse` + turunannya | Cuma dipanggil lewat `observability/tracing.py` untuk kirim trace — tidak ada di jalur request/response sama sekali. |
| `rabbitmq`/`async-worker` (Profil A) | Cuma dipakai jalur `POST /v1/agent/jobs`; `/internal/v1/runs` (Profil A) dan `/v1/agent/invoke` (Profil B) tidak menyentuhnya — **kecuali** lewat gateway (Profil B), gateway tetap connect ke RabbitMQ saat startup (`main.py`'s `lifespan`) meski hanya dipakai sync, makanya Profil B tetap mencantumkannya. |
| `services/ingestion` | Cuma proses tulis (re-ingest). Data yang sudah pernah di-ingest tetap ada di `postgres` (volume `pgdata` persisten) selama volume tidak dihapus (`make down`, bukan `make clean`). |

**Yang TIDAK aman di-skip**: `mock-business-api`. Beda dari RAG/cache, panggilan tool
(`tools/executor.py` → `clients/business_api.py`) cuma menangkap `BusinessApiError` (respons HTTP
dengan status buruk) — **bukan** kegagalan koneksi total, jadi kalau service-nya benar-benar tidak
jalan dan model memutuskan memanggil tool (untuk `hr-assistant`, ini sering terjadi bahkan untuk
pertanyaan yang tidak relevan — lihat CLAUDE.md's M7 known-quirks), turn itu akan gagal 503, bukan
degraded. Untung ukurannya kecil (~13MB RAM di `docker stats`), jadi selalu disertakan di kedua
profil di atas — tidak ada alasan kuat untuk membuangnya.

## Mematikan hanya yang tadi dinyalakan

```bash
# Profil A
docker compose -f deploy/docker-compose.yml --env-file .env stop \
  postgres redis ollama mock-business-api model-router agent-harness

# Profil B (tambahan di atas Profil A)
docker compose -f deploy/docker-compose.yml --env-file .env stop \
  rabbitmq mock-idp agent-gateway kong
```

(`stop`, bukan `down` — biar `pgdata` & data lain tetap ada untuk sesi demo berikutnya. Container
yang di-`stop` tetap ada, cuma tidak jalan; `docker compose up -d` atau `make up` berikutnya akan
menyalakannya lagi apa adanya, bukan membuat ulang dari nol.)
