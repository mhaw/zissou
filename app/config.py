from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class FirebaseAuthConfig:
    """Typed configuration for Firebase Authentication."""

    api_key: str | None
    auth_domain: str | None
    project_id: str | None

    @classmethod
    def from_env(cls) -> FirebaseAuthConfig:
        """Create a FirebaseAuthConfig from environment variables."""
        return cls(
            api_key=os.environ.get("FIREBASE_WEB_API_KEY"),
            auth_domain=os.environ.get("FIREBASE_AUTH_DOMAIN"),
            project_id=os.environ.get("FIREBASE_PROJECT_ID"),
        )

    @property
    def is_valid(self) -> bool:
        """Check if the configuration is valid."""
        return all([self.api_key, self.auth_domain, self.project_id])

    def to_dict(self) -> dict[str, str | None]:
        """Convert the configuration to a dictionary."""
        return {
            "apiKey": self.api_key,
            "authDomain": self.auth_domain,
            "projectId": self.project_id,
        }


@dataclass(frozen=True)
class CSRFConfig:
    """Configuration values for CSRF protection."""

    secret_key: str
    time_limit_seconds: int

    @classmethod
    def from_env(cls) -> CSRFConfig:
        """Create a CSRFConfig from environment variables with sensible defaults."""
        raw_time_limit = os.getenv("CSRF_TIME_LIMIT", "43200")  # 12 hours default
        try:
            time_limit = int(raw_time_limit)
        except ValueError:
            time_limit = 43200
        return cls(
            secret_key=os.getenv("CSRF_SECRET_KEY", "a-different-secret-key"),
            time_limit_seconds=time_limit,
        )


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ENV: str = "development"
    AUTH_ENABLED: bool = False
    GCP_PROJECT_ID: str | None = None
    GCS_BUCKET: str | None = None
    ADMIN_EMAILS: str = ""
    ALLOWED_ORIGINS: str = ""


settings = AppSettings()


class AppConfig:
    FIRESTORE_COLLECTION_ITEMS = os.getenv("FIRESTORE_COLLECTION_ITEMS", "items")
