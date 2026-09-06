import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_voice_detection_returns_gradio_result_and_removes_temp_file():
    observed_path = None

    def predict(**kwargs):
        nonlocal observed_path
        observed_path = kwargs["audio_path"]["path"]
        assert os.path.exists(observed_path)
        return {
            "success": True,
            "fake_probability": 0.8155,
            "bonafide_score": 0.1845,
            "verdict": "FAKE",
        }

    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.side_effect = predict
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "fake_probability": 0.8155,
        "bonafide_score": 0.1845,
        "verdict": "FAKE",
    }
    assert observed_path is not None
    assert not os.path.exists(observed_path)
    client_class.return_value.predict.assert_called_once()


def test_voice_detection_rejects_non_mp3_without_calling_gradio():
    with patch("app.routers.voice_detection.Client") as client_class:
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.wav", b"wav bytes", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Voice detection failed",
    }
    client_class.assert_not_called()


def test_voice_detection_returns_failure_for_unexpected_gradio_result():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = {"success": True}
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Voice detection failed",
    }


def test_voice_detection_returns_failure_for_invalid_scores():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": "0.1",
            "bonafide_score": 0.9,
            "verdict": "REAL",
        }
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Voice detection failed",
    }