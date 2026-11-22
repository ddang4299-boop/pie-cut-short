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
    llm_provider: str = "claude_code"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    ollama_model: str = "llama3.1"
    ollama_host: str = "http://127.0.0.1:11434"
    whisper_model: str = "large-v3"
    device: str = "auto"  # auto | cuda | mps | cpu
    data_dir: Path = Path("data")
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    @property
    def data_path(self) -> Path:
        """Absolute data directory (relative paths are resolved from the repo root)."""
        path = self.data_dir if self.data_dir.is_absolute() else REPO_ROOT / self.data_dir
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
