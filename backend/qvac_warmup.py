"""
Prepara el modelo QVAC en este equipo.

EJECUCIÓN:
    python qvac_warmup.py

La primera ejecución puede necesitar internet para descargar el modelo.
Después de que termine correctamente, desconecta internet y vuelve a
ejecutar este mismo archivo para comprobar que la inferencia funciona
desde el cache local.
"""

from __future__ import annotations

import asyncio
import json

from ai import (
    initialize_qvac,
    shutdown_qvac,
    extract_observation,
    get_ai_runtime_status,
)


TEST_TEXT = (
    "Visité Hospital DemoCare Pacific en Ciudad de Panamá, Panamá. "
    "Tienen dos equipos MR Siemens de aproximadamente 8 años y un CT Philips."
)


async def main():
    try:
        print("=== INICIALIZANDO QVAC LOCAL ===")
        await initialize_qvac()

        print("\n=== ESTADO ===")
        print(
            json.dumps(
                get_ai_runtime_status(),
                ensure_ascii=False,
                indent=2,
            )
        )

        print("\n=== PRUEBA DE EXTRACCIÓN ===")
        result = await extract_observation(TEST_TEXT)

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            )
        )

        equipment = {
            str(item.get("modality") or "").upper(): item
            for item in result.get("equipment", [])
        }
        mr = equipment.get("MR")
        ct = equipment.get("CT")

        checks = [
            (mr is not None, "Debe existir MR"),
            (ct is not None, "Debe existir CT"),
            (mr is not None and mr.get("estimated_age") == 8.0, "MR debe conservar edad 8"),
            (ct is not None and ct.get("estimated_age") is None, "CT debe conservar edad desconocida"),
            (mr is not None and mr.get("model") is None, "Siemens no debe copiarse como modelo"),
            (ct is not None and ct.get("model") is None, "Philips no debe copiarse como modelo"),
        ]

        failures = [message for ok, message in checks if not ok]
        if failures:
            raise RuntimeError(
                "Validación estructurada falló: " + "; ".join(failures)
            )

        status = get_ai_runtime_status()
        if status.get("cloud_inference") is not False or not status.get("qvac_ready"):
            raise RuntimeError("El estado del runtime no confirma QVAC local listo.")

        print("\nOK: extracción estructurada validada (MR=8, CT=null, modelos=null).")
        print("OK: QVAC realizó la extracción en este dispositivo.")

    finally:
        await shutdown_qvac()


if __name__ == "__main__":
    asyncio.run(main())
