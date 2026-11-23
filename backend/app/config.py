"""App settings, loaded from environment variables and the repo-root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    elevenlabs_api_key: str = ""
    elevenlabs_mock: bool = False  # ELEVENLABS_MOCK=1: offline fake client (tones), never calls the API
    llm_provider: str = "claude_code"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    ollama_model: str = "llama3.1"
    ollama_host: str = "http://127.0.0.1:11434"
    whisper_model: str = "large-v3"
    whisper_models_dir: Path = Path("data/models")  # faster-whisper downloads models here
    device: str = "auto"  # auto | cuda | mps | cpu
    data_dir: Path = Path("data")
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    start_job_workers: bool = True  # tests switch this off and drive the queue by hand
    serve_frontend: bool = False  # desktop mode: also serve frontend/dist and accept the /api prefix
    frontend_dist: Path = Path("frontend/dist")

    @staticmethod
    def _abs(path: Path) -> Path:
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def data_path(self) -> Path:
        """Absolute data directory (relative paths are resolved from the repo root)."""
        return self._abs(self.data_dir)

    @property
    def frontend_dist_path(self) -> Path:
        return self._abs(self.frontend_dist)

    @property
    def models_path(self) -> Path:
        return self._abs(self.whisper_models_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "shortforge.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
