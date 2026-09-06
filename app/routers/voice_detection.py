import math
import json
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, UploadFile
from gradio_client import Client, handle_file


router = APIRouter(tags=["Voice Detection"])
logger = logging.getLogger(__name__)


def _failure_response() -> Dict[str, Any]:
    return {"success": False, "message": "Voice detection failed"}


def _validate_result(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict) or result.get("success") is not True:
        return None

    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"REAL", "FAKE"}:
        return None

    scores = (result.get("fake_probability"), result.get("bonafide_score"))
    if any(
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        for score in scores
    ):
        return None

    return {
        "success": True,
        "fake_probability": result["fake_probability"],
        "bonafide_score": result["bonafide_score"],
        "verdict": result["verdict"],
    }


def _parse_result(value: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    if depth > 4:
        return None

    validated = _validate_result(value)
    if validated is not None:
        return validated

    if isinstance(value, str):
        try:
            return _parse_result(json.loads(value), depth + 1)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _parse_result(item, depth + 1)
            if parsed is not None:
                return parsed
        return None

    if isinstance(value, dict):
        for item in value.values():
            parsed = _parse_result(item, depth + 1)
            if parsed is not None:
                return parsed
    return None


@router.post("/voice-detection")
def detect_voice(file: Optional[UploadFile] = File(None)) -> Dict[str, Any]:
    if file is None or not file.filename or not file.filename.lower().endswith(".mp3"):
        return _failure_response()

    temporary_path = None
    try:
        audio_bytes = file.file.read()
        logger.info(
            "Voice detection upload: filename=%r content_type=%r size=%d",
            file.filename,
            file.content_type,
            len(audio_bytes),
        )
        if not audio_bytes:
            return _failure_response()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporary_file:
            temporary_file.write(audio_bytes)
            temporary_path = temporary_file.name
        logger.info("Voice detection temporary file: path=%r", temporary_path)

        client = Client("Juek/AI_Voice_Detection")
        result = client.predict(
            audio_path=handle_file(temporary_path),
            api_name="/detect_voice",
        )
        logger.info(
            "Voice detection Gradio result: type=%s repr=%r",
            type(result).__name__,
            result,
        )
        return _parse_result(result) or _failure_response()
    except Exception:
        logger.exception("Voice detection failed while calling Gradio")
        return _failure_response()
    finally:
        file.file.close()
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except OSError:
                pass