import asyncio
from types import SimpleNamespace

import pytest
import ai


@pytest.mark.parametrize("wrapper", [
    '{"customer_name":"Demo", "equipment":[{"model":"A}B"}]}',
    '```json\n{"customer_name":"Demo", "equipment":[{"model":"A}B"}]}\n```',
    'Result: {"customer_name":"Demo", "equipment":[{"model":"A}B"}]} Done.',
    '{"customer_name":"Demo", "equipment":[{"model":"A}B"}]} {"customer_name":"Other"}',
])
def test_model_json_wrappers_preserve_first_complete_object(wrapper):
    assert ai._extract_json_object(wrapper) == {
        "customer_name": "Demo", "equipment": [{"model": "A}B"}]}


@pytest.mark.parametrize("content", [
    '', 'null', '[]', '[{"customer_name":"Demo"}]',
    '{invalid: {"customer_name":"Demo"}}',
])
def test_model_json_rejects_malformed_or_non_object(content):
    with pytest.raises(ValueError):
        ai._extract_json_object(content)


def test_runtime_calls_are_serialized(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ai, "_runtime_lock", asyncio.Lock())
        active = peak = 0
        async def operation():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.01)
            active -= 1
        guarded = ai._serialized(operation, 1)
        await asyncio.gather(guarded(), guarded(), guarded())
        assert peak == 1
    asyncio.run(scenario())


def test_timeout_releases_worker_and_lock(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ai, "_runtime_lock", asyncio.Lock())
        calls = []
        async def close():
            calls.append("closed")
        async def hanging():
            await asyncio.sleep(1)
        monkeypatch.setattr(ai, "_shutdown_unlocked", close)
        with pytest.raises(ai.QvacNotReadyError, match="tiempo límite"):
            await ai._serialized(hanging, .01)()
        assert calls == ["closed"] and not ai._runtime_lock.locked()
    asyncio.run(scenario())


def test_failed_initialization_closes_client(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        async def connect(self, **kwargs):
            raise RuntimeError("worker failed")
        async def __aexit__(self, *args):
            calls.append("closed")
    monkeypatch.setattr(ai, "Client", Client)
    monkeypatch.setattr(ai, "QVAC_ENABLED", True)
    monkeypatch.setattr(ai, "qvac_models", SimpleNamespace(**{ai.QVAC_MODEL: "model"}))
    monkeypatch.setattr(ai, "_qvac_ready", False)
    monkeypatch.setattr(ai, "_runtime_lock", asyncio.Lock())
    with pytest.raises(ai.QvacNotReadyError, match="worker failed"):
        asyncio.run(ai.initialize_qvac())
    assert calls == ["closed"]
    assert ai._qvac_client is None and not ai._qvac_ready


def test_inference_worker_failure_clears_ready_status(monkeypatch):
    async def scenario():
        calls = []
        class Client:
            async def __aexit__(self, *args):
                calls.append("closed")
        async def unload(*args):
            calls.append("unloaded")
        def fail(*args, **kwargs):
            raise ConnectionError("worker disconnected")
        monkeypatch.setattr(ai, "_runtime_lock", asyncio.Lock())
        monkeypatch.setattr(ai, "_qvac_ready", True)
        monkeypatch.setattr(ai, "_qvac_transport", object())
        monkeypatch.setattr(ai, "_qvac_model_id", "test-model")
        monkeypatch.setattr(ai, "_qvac_asr_model_id", None)
        monkeypatch.setattr(ai, "_qvac_client", Client())
        monkeypatch.setattr(ai, "completion", fail)
        monkeypatch.setattr(ai, "unload_model", unload)
        with pytest.raises(ai.QvacNotReadyError, match="worker disconnected"):
            await ai.extract_observation("Hospital Demo tiene un CT.")
        assert not ai.get_ai_runtime_status()["qvac_ready"]
        assert ai.get_ai_runtime_status()["last_inference_note"] == "qvac_runtime_error"
        assert calls == ["unloaded", "closed"]
    asyncio.run(scenario())


def test_initialization_timeout_keeps_timeout_diagnostic(monkeypatch):
    async def scenario():
        calls = []
        class Client:
            def __init__(self, **kwargs):
                pass
            async def connect(self, **kwargs):
                await asyncio.sleep(10)
            async def __aexit__(self, *args):
                calls.append("closed")
        monkeypatch.setattr(ai, "Client", Client)
        monkeypatch.setattr(ai, "QVAC_ENABLED", True)
        monkeypatch.setattr(ai, "qvac_models", SimpleNamespace(**{ai.QVAC_MODEL: "model"}))
        monkeypatch.setattr(ai, "_qvac_ready", False)
        monkeypatch.setattr(ai, "_runtime_lock", asyncio.Lock())
        # An outer deadline models shutdown/cancellation during startup.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ai.initialize_qvac(), timeout=.02)
        assert calls == ["closed"] and not ai._qvac_ready
        assert not ai._runtime_lock.locked()
    asyncio.run(scenario())
