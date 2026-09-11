"""
Prepara el modelo de voz QVAC (WHISPER_TINY) en este equipo.

Primera ejecución: mantén internet disponible para que QVAC pueda descargar
el modelo si todavía no está en cache. Después puedes desconectarte y usar
el dictado local desde Fieldscope.
"""

from __future__ import annotations

import asyncio
import json
import argparse
from pathlib import Path

from ai import (
    get_ai_runtime_status,
    initialize_qvac,
    initialize_qvac_asr,
    shutdown_qvac,
    transcribe_audio_file,
)


async def main(audio: str | None = None) -> None:
    try:
        print("=== INICIALIZANDO QVAC TEXTO ===")
        await initialize_qvac()

        print("\n=== PREPARANDO QVAC ASR LOCAL ===")
        await initialize_qvac_asr()

        if audio:
            transcript = await transcribe_audio_file(str(Path(audio).resolve()))
            print("Transcripción real: " + transcript)

        print("\n=== ESTADO ===")
        print(
            json.dumps(
                get_ai_runtime_status(),
                ensure_ascii=False,
                indent=2,
            )
        )

        print("\nOK: WHISPER_TINY quedó preparado para dictado QVAC local.")
        if not audio:
            print("Solo se verificó la carga del modelo; no se probó transcripción. Usa --audio archivo.wav.")
    finally:
        await shutdown_qvac()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", help="WAV PCM16 mono 16 kHz para una prueba real de transcripción")
    asyncio.run(main(parser.parse_args().audio))
