from pathlib import Path
import logging

import requests

from app.core.config import get_settings
from app.services.media import MediaService


settings = get_settings()
logger = logging.getLogger("app.deepgram")


class DeepgramTranscriptionService:
    def __init__(self) -> None:
        self.media_service = MediaService()

    def transcribe(self, source_path: str) -> dict:
        if not settings.deepgram_api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is missing. Add it to your .env before uploading.")

        logger.info("deepgram_transcription_start source=%s model=%s", source_path, settings.deepgram_model)
        wav_path = self.media_service.normalize_to_wav(source_path)
        logger.info("deepgram_audio_normalized wav=%s", wav_path)
        response = self._submit_to_deepgram(wav_path)
        logger.info("deepgram_response_received wav=%s", wav_path)
        return self._normalize_response(response)

    def _submit_to_deepgram(self, wav_path: str) -> dict:
        params = {
            "model": settings.deepgram_model,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
            "utt_split": "0.8",
            "diarize": "true",
        }
        headers = {
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "audio/wav",
        }

        file_size = Path(wav_path).stat().st_size
        logger.info("deepgram_payload_ready wav=%s wav_size_bytes=%s", wav_path, file_size)

        with open(wav_path, "rb") as audio_file:
            audio_bytes = audio_file.read()
            logger.info(
                "deepgram_request_start model=%s url=%s bytes=%s",
                settings.deepgram_model,
                settings.deepgram_api_url,
                len(audio_bytes),
            )
            response = requests.post(
                settings.deepgram_api_url,
                params=params,
                headers=headers,
                data=audio_bytes,
                timeout=(30, 300),
            )

        logger.info("deepgram_request_complete status=%s", response.status_code)
        response.raise_for_status()
        return response.json()

    def _normalize_response(self, payload: dict) -> dict:
        results = payload.get("results", {})
        utterances = results.get("utterances") or []

        if utterances:
            logger.info("deepgram_utterances_received count=%s", len(utterances))
            chunks = [
                {
                    "start": float(item.get("start", 0.0)),
                    "end": float(item.get("end", 0.0)),
                    "text": str(item.get("transcript", "")).strip(),
                }
                for item in utterances
                if str(item.get("transcript", "")).strip()
            ]
            full_text = " ".join(chunk["text"] for chunk in chunks).strip()
            return {"text": full_text, "chunks": chunks}

        channels = results.get("channels") or []
        alternatives = channels[0].get("alternatives", []) if channels else []
        transcript = str(alternatives[0].get("transcript", "")).strip() if alternatives else ""
        words = alternatives[0].get("words", []) if alternatives else []

        if words:
            logger.info("deepgram_words_received count=%s", len(words))
            chunks = [
                {
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", 0.0)),
                    "text": str(word.get("punctuated_word") or word.get("word") or "").strip(),
                }
                for word in words
                if str(word.get("punctuated_word") or word.get("word") or "").strip()
            ]
            return {"text": transcript, "chunks": chunks}

        logger.info("deepgram_transcript_without_breakdown")
        return {"text": transcript, "chunks": [{"start": 0.0, "end": 0.0, "text": transcript}] if transcript else []}
