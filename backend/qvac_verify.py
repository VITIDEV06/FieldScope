"""Manual real-model regression checks. No database writes or cloud inference."""
import asyncio
import json
from ai import initialize_qvac, shutdown_qvac, extract_observation, get_ai_runtime_status


async def main():
    try:
        await initialize_qvac()
        cases = [
            ("Hospital Demo tiene un CT Philips.", "CT", None),
            ("Hospital Demo tiene un CT Philips Incisive.", "CT", "Incisive"),
            ("Hospital Demo tiene un tomógrafo Philips.", "CT", None),
            ("Hospital Demo tiene dos equipos de rayos X.", "X-Ray", None),
        ]
        for text, modality, model in cases:
            result = await extract_observation(text)
            assert len(result["equipment"]) == 1, result
            eq = result["equipment"][0]
            assert eq["modality"] == modality and eq["model"] == model, result
            assert get_ai_runtime_status()["last_inference_note"] == "qvac_local"
            print(f"PASS: {modality}, model={model!r}")
        context = {"customer_name": "Hospital Demo", "city": None, "country": None,
                   "_question_key": "equipment.0.manufacturer", "equipment": [
                       {"modality": "CT", "manufacturer": None, "model": None,
                        "quantity": 2, "estimated_age": None, "state": "Reportado"}]}
        result = await extract_observation("Philips.", context)
        assert result["equipment"][0]["manufacturer"] == "Philips", result
        assert result["equipment"][0]["quantity"] == 2, result
        print("PASS: contextual manufacturer follow-up")
        print(json.dumps(get_ai_runtime_status(), ensure_ascii=False, indent=2))
    finally:
        await shutdown_qvac()


if __name__ == "__main__":
    asyncio.run(main())
