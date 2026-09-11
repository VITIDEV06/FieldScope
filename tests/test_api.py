import datetime
import io
import wave

import pytest
import main
from models import Customer, Equipment, Observation

pytestmark = pytest.mark.integration
DEMO = "Visité Hospital DemoCare Pacific en Ciudad de Panamá, Panamá. Tienen dos equipos MR Siemens de aproximadamente ocho años y un CT Philips."


def test_capture_follow_up_save_views_delete(client):
    body = client.post("/api/observations/process", json={"text": DEMO, "submitted_by": "Demo Ana"}).json()
    assert body["status"] == "needs_more_info"
    assert "antigüedad" in body["follow_up"] and "CT" in body["follow_up"]
    session = body["session_id"]
    body = client.post("/api/observations/process", json={"text": "No sé", "session_id": session}).json()
    assert body["status"] == "needs_more_info"
    body = client.post("/api/observations/process", json={"text": "No recuerdo", "session_id": session}).json()
    assert body["status"] == "ready_for_confirmation"
    assert client.get("/api/analytics/dashboard").json()["total_equipment"] == 0
    saved = client.post("/api/observations/confirm", json={"session_id": session}).json()
    assert saved["status"] == "success"
    cid = saved["customer"]["id"]
    detail = client.get(f"/api/customers/{cid}").json()
    assert len(detail["equipment"]) == 2 and len(detail["observations"]) == 1
    assert detail["customer"]["country"] == "Panamá"
    assert detail["equipment"][0]["observation_state"] == "Estimado"
    assert detail["equipment"][1]["observation_state"] == "Reportado"
    dashboard = client.get("/api/analytics/dashboard").json()
    assert dashboard["total_equipment"] == 3
    assert dashboard["incomplete_customers"] == 1
    assert dashboard["aging_technology_customers"] == 1
    assert len(client.get("/api/opportunities").json()["items"]) == 1
    assert client.post("/api/observations/confirm", json={"session_id": session}).status_code == 404
    assert client.delete(f'/api/observations/{saved["observation_id"]}').json()["equipment_preserved"]
    detail = client.get(f"/api/customers/{cid}").json()
    assert len(detail["equipment"]) == 2 and detail["observations"] == []
    assert client.get("/api/analytics/dashboard").json()["total_observations"] == 0


def test_short_manufacturer_followup(client):
    body = client.post("/api/observations/process", json={"text": "Hospital Demo tiene dos CT."}).json()
    assert "fabricante" in body["follow_up"]
    body = client.post("/api/observations/process", json={"text": "Philips.", "session_id": body["session_id"]}).json()
    assert body["extracted_so_far"]["equipment"][0]["manufacturer"] == "Philips"
    assert "antigüedad" in body["follow_up"]


def test_duplicate_requires_independent_reporter(save, client):
    text = "Hospital Demo tiene un CT Philips Incisive de 5 años."
    first = save(text, "Demo Ana")
    same = save(text, "Demo Ana")
    independent = save(text, "Demo Luis")
    assert same["equipment"][0]["id"] == first["equipment"][0]["id"]
    assert same["equipment"][0]["observation_state"] == "Reportado"
    assert same["equipment"][0]["confidence_score"] == first["equipment"][0]["confidence_score"]
    assert independent["equipment"][0]["observation_state"] == "Confirmado"
    assert independent["equipment"][0]["times_reported"] == 3
    assert independent["contains_duplicate_update"]


@pytest.mark.parametrize("second", ["un CT Philips de 20 años", "dos CT Philips de 5 años", "un CT Siemens de 5 años"])
def test_conflicting_equipment_not_merged(save, second):
    first = save("Hospital Demo tiene un CT Philips de 5 años.")
    other = save("Hospital Demo tiene " + second)
    assert other["equipment"][0]["id"] != first["equipment"][0]["id"]


def test_ambiguous_modality_only_not_merged(save):
    first = save("Hospital Demo tiene tres MR.")
    other = save("Hospital Demo tiene tres MR.")
    assert other["equipment"][0]["id"] != first["equipment"][0]["id"]


def test_customer_identity_includes_location(save, client):
    save("Hospital Demo en Lima, Perú tiene un MR.")
    save("Hospital Demo en Santiago, Chile tiene un MR.")
    assert len(client.get("/api/customers").json()) == 2


def test_geography_and_word_number_query(save, client):
    save("Hospital Demo Brazil en São Paulo, Brazil tiene un MR Siemens de 8 años.")
    catalog = client.get("/api/geography/catalog").json()
    assert "Brasil" in catalog["regions"]["Sudamérica"]
    body = client.post("/api/queries/local", json={"query": "Muéstrame clientes en Brasil con MR de más de siete años."}).json()
    assert body["count"] == 1 and body["items"][0]["country"] == "Brasil"
    assert client.post("/api/queries/local", json={"query": "inventar ingresos"}).status_code == 422
    assert client.post("/api/queries/local", json={"query": "MR de menos de siete años"}).status_code == 422
    custom = client.post("/api/geography/custom-countries", json={"country_name": "Isla Demo", "region": "Oceanía"})
    assert custom.status_code == 200
    assert len(client.get("/api/geography/custom-countries").json()) == 1


def test_queries_reject_unrecognized_constraints(save, client):
    save("Hospital Demo tiene un MR Siemens de 8 años y un CT Philips de 9 años.")
    for query in ["MR en Atlantis", "MR de marca Inventada", "MR o CT", "equipos Siemens y Philips", "MR con contrato vencido"]:
        response = client.post("/api/queries/local", json={"query": query})
        assert response.status_code == 422, (query, response.text)
    assert client.post("/api/queries/local", json={"query": "¿Qué clientes tienen equipos Philips?"}).json()["count"] == 1


def test_sql_filters_do_not_execute_input(save, client):
    save("Hospital Demo tiene un CT Philips.")
    response = client.get("/api/installed-base", params={"customer": "'; DROP TABLE customers; --"})
    assert response.status_code == 200 and response.json()["count"] == 0
    assert len(client.get("/api/customers").json()) == 1


def test_health_security_validation(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["cloud_inference"] is False
    assert health["qvac_ready"] is False
    assert "cache_directory" not in health
    assert client.get("/api/capabilities").json()["voice"]["implemented"]
    for text in ("", " " * 3, "x" * 4001):
        assert client.post("/api/observations/process", json={"text": text}).status_code == 422
    assert client.get("/api/customers?limit=-1").status_code == 422
    assert client.post("/api/observations/process", json={"text": "hello"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    response = client.options("/api/observations/process", headers={"Origin": "http://127.0.0.1:5500", "Access-Control-Request-Method": "POST"})
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5500"


@pytest.mark.parametrize("days,status", [(179,"fresh"),(180,"aging"),(365,"aging"),(366,"stale")])
def test_freshness_thresholds(client, db, save, days, status):
    saved = save("Hospital Demo tiene un CT.")
    eq = db.get(Equipment, saved["equipment"][0]["id"])
    eq.last_verified = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days)
    db.commit()
    item = client.get("/api/installed-base").json()["items"][0]
    assert item["freshness"] == status


def wav_bytes(rate=16000, channels=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\0\0" * rate * channels)
    return out.getvalue()


@pytest.mark.parametrize("content,mime,status", [
    (b"audio", "text/plain", 415), (b"", "audio/wav", 400),
    (b"RIFF0000WAVE"+b"\0"*40, "audio/wav", 400),
    (b"0"*(2*1024*1024+1), "audio/wav", 413),
    (wav_bytes(48000), "audio/wav", 400), (wav_bytes(channels=2), "audio/wav", 400),
    (wav_bytes()[:-2], "audio/wav", 400),
], ids=["wrong-mime", "empty", "fake-header", "oversized", "wrong-rate", "stereo", "truncated"])
def test_voice_validation(client, content, mime, status):
    assert client.post("/api/voice/transcribe", content=content, headers={"content-type": mime}).status_code == status


def test_voice_contract_and_cleanup(client, monkeypatch):
    from pathlib import Path
    paths = []
    async def fake(path):
        assert Path(path).exists()
        paths.append(Path(path))
        return "Hospital Demo tiene un CT."
    monkeypatch.setattr(main, "transcribe_audio_file", fake)
    for _ in range(2):
        result = client.post("/api/voice/transcribe", content=wav_bytes(), headers={"content-type": "audio/wav"})
        assert result.status_code == 200
        assert result.json()["provider"] == "QVAC"
    assert all(not path.exists() for path in paths)


def test_migrations_idempotent(client):
    main.run_database_migrations()
    main.run_database_migrations()
    assert client.get("/api/analytics/dashboard").json()["total_customers"] == 0


def test_seed_is_atomic_synthetic_and_opt_in(client):
    result = client.post("/api/seed").json()
    assert result["seeded"] and result["customers"] == 9
    result = client.post("/api/seed").json()
    assert result["seeded"] is False
    customers = client.get("/api/customers").json()
    assert all("Demo" in row["name"] for row in customers)
    dashboard = client.get("/api/analytics/dashboard").json()
    assert dashboard["total_observations"] == 9
    assert dashboard["unknown_quantity_records"] == 1
    assert dashboard["freshness"]["unknown"] == 1


def test_sessions_cannot_ask_forever(client):
    body = client.post("/api/observations/process", json={"text": "Hay tres MR."}).json()
    session = body["session_id"]
    for _ in range(5):
        body = client.post("/api/observations/process", json={"text": "No sé", "session_id": session}).json()
    assert body["status"] == "error"
    assert client.post("/api/observations/process", json={"text": "No sé", "session_id": session}).status_code == 409
    assert client.delete(f"/api/observations/pending/{session}").status_code == 200


def test_unknown_quantity_persists_as_unknown(save, client):
    saved = save("Hospital Demo tiene MR.")
    assert saved["equipment"][0]["quantity"] is None
    assert client.get("/api/equipment").json()[0]["quantity"] is None
    assert client.get("/api/analytics/dashboard").json()["total_equipment"] == 0


def test_legacy_migration_preserves_unknown_dates(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    legacy = create_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    with legacy.begin() as connection:
        connection.execute(text("CREATE TABLE equipment (id INTEGER PRIMARY KEY, modality VARCHAR, quantity INTEGER)"))
        connection.execute(text("INSERT INTO equipment VALUES (1, 'CT', 2)"))
        connection.execute(text("CREATE TABLE observations (id INTEGER PRIMARY KEY, submitted_at DATETIME)"))
    monkeypatch.setattr(main, "engine", legacy)
    main.run_database_migrations()
    main.run_database_migrations()
    assert "serial_number" in {column["name"] for column in inspect(legacy).get_columns("equipment")}
    with legacy.connect() as connection:
        assert connection.execute(text("SELECT modality, quantity, last_verified FROM equipment")).one() == ("CT", 2, None)
    legacy.dispose()
