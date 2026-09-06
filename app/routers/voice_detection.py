import math
import os
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, UploadFile
from gradio_client import Client, handle_file


router = APIRouter(tags=["Voice Detection"])


def _failure_response() -> Dict[str, Any]:
    return {"success": False, "message": "Voice detection failed"}


def _normalize_result(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict) or result.get("success") is not True:
        return None

    verdict = result.get("verdict")
    if not isinstance(verdict, str) or not verdict.strip():
        return None

    normalized_verdict = verdict.strip().upper()
    confidence_key = {
        "FAKE": "fake_probability",
        "BONAFIDE": "bonafide_score",
    }.get(normalized_verdict)
    if confidence_key is None:
        return None

    confidence = result.get(confidence_key)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not math.isfinite(confidence):
        return None

    return {
        "success": True,
        "voice_type": verdict.strip(),
        "confidence": confidence,
    }


@router.post("/voice-detection")
def detect_voice(file: Optional[UploadFile] = File(None)) -> Dict[str, Any]:
    if file is None or not file.filename or not file.filename.lower().endswith(".mp3"):
        return _failure_response()

    temporary_path = None
    try:
        audio_bytes = file.file.read()
        if not audio_bytes:
            return _failure_response()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporary_file:
            temporary_file.write(audio_bytes)
            temporary_path = temporary_file.name

        client = Client("Juek/AI_Voice_Detection")
        result = client.predict(
            audio_path=handle_file(temporary_path),
            api_name="/detect_voice",
        )
        return _normalize_result(result) or _failure_response()
    except Exception:
        return _failure_response()
    finally:
        file.file.close()
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except OSError:
                pass