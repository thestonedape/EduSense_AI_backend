from pathlib import Path
import shutil

import ffmpeg

from app.core.config import get_settings


settings = get_settings()


class MediaService:
    def _ffmpeg_command(self) -> str:
        configured = settings.ffmpeg_binary.strip()
        if configured and shutil.which(configured):
            return configured
        if configured and configured.lower().endswith(".exe"):
            return configured
        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered
        raise RuntimeError("ffmpeg is not available on PATH. Install ffmpeg or set FFMPEG_BINARY.")

    def extract_audio(self, source_path: str, target_path: str) -> str:
        (
            ffmpeg.input(source_path)
            .output(target_path, ac=1, ar=16000)
            .overwrite_output()
            .run(cmd=self._ffmpeg_command(), quiet=True)
        )
        return target_path

    def normalize_to_wav(self, source_path: str) -> str:
        source = Path(source_path)
        if source.suffix.lower() == ".wav":
            return str(source)
        target = source.with_suffix(".wav")
        return self.extract_audio(str(source), str(target))
