"""Smoke test local del MVP SIN afirmar QVAC real.

Usa QVAC_REQUIRED=false y una SQLite temporal para probar el flujo de datos.
Para probar QVAC real usa qvac_warmup.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

fd, db_path = tempfile.mkstemp(prefix="fieldscope-smoke-", suffix=".db")
os.close(fd)
os.unlink(db_path)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["QVAC_REQUIRED"] = "false"

from database import SessionLocal, engine  # noqa: E402
from main import (  # noqa: E402
    ObservationRequest,
    ConfirmObservationRequest,
    NaturalQueryRequest,
    process_observation,
    confirm_observation,
    query_local_dataset,
    get_renewal_opportunities,
)


async def main() -> None:
    db = SessionLocal()
    try:
        response = await process_observation(
            ObservationRequest(
                text=(
                    "Estoy en Hospital DemoCare Pacific en Panamá. "
                    "Vi un tomógrafo Philips y un resonador que parece tener unos 8 años."
                )
            ),
            db,
        )
        assert response["status"] == "needs_more_info"
        session_id = response["session_id"]

        response = await process_observation(
            ObservationRequest(text="No recuerdo el modelo", session_id=session_id), db
        )
        assert response["status"] == "needs_more_info"

        response = await process_observation(
            ObservationRequest(text="No sé", session_id=session_id), db
        )
        assert response["status"] == "ready_for_confirmation"

        saved = confirm_observation(ConfirmObservationRequest(session_id=session_id), db)
        assert saved["status"] == "success"
        assert saved["customer"]["country"] == "Panamá"

        query = query_local_dataset(
            NaturalQueryRequest(query="¿Qué resonadores tienen más de 7 años?"), db
        )
        assert query["count"] == 1
        assert query["items"][0]["modality"] == "MR"

        opportunities = get_renewal_opportunities(7, db)
        assert len(opportunities["items"]) == 1

        print("OK: smoke test local del MVP completado.")
        print(json.dumps({
            "saved_customer": saved["customer"]["name"],
            "query_results": query["count"],
            "opportunities": len(opportunities["items"]),
            "note": "Este test usa fallback determinístico, no QVAC real.",
        }, ensure_ascii=False, indent=2))
    finally:
        db.close()
        engine.dispose()
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
