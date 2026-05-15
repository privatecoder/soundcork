from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Create the settings.

    Don't populate here. The variables are only declared to make life
    easier for IDE autocomplete. Populate in .env.shared -- or, if
    committing to source control, .env.private (which is in the
    .gitignore).

    All environment variables follow the UPPER_SNAKE_CASE convention.
    The Python attributes stay lowercase per PEP 8 — the
    `validation_alias` on each field maps the env-var name to the
    attribute name.

    Source for each of these strings:

    Unless otherwise specified all files are on you speaker in:
    /var/volatile/lib/Bose/PersistenceDataRoot/BoseApp-Persistence/1

    - device_id: Recents.xml

    """

    # URL the speakers use to reach soundcork. Must be device-reachable
    # (e.g. host LAN IP) — not localhost or a container name.
    base_url: str = Field("", validation_alias="BASE_URL")

    # Local directory where soundcork stores Accounts.json, Sources.xml,
    # Presets.xml, Recents.xml, and per-device data.  Defaults to ./data
    # relative to whatever directory the server is launched from.  For
    # Docker, override with an absolute path that matches your volume
    # mount (e.g. DATA_DIR=/soundcork/data).
    data_dir: str = Field("./data", validation_alias="DATA_DIR")

    # Spotify OAuth (optional — leave empty to disable).
    spotify_client_id: str = Field("", validation_alias="SPOTIFY_CLIENT_ID")
    spotify_client_secret: str = Field("", validation_alias="SPOTIFY_CLIENT_SECRET")
    spotify_redirect_uri: str = Field("", validation_alias="SPOTIFY_REDIRECT_URI")

    # (optional) Local directory for soundcork to store detailed logs of
    # 404/unhandled requests. Used for development/debugging.
    unhandled_log_dir: str = Field("", validation_alias="UNHANDLED_LOG_DIR")

    model_config = SettingsConfigDict(
        # `.env.private` takes priority over `.env.shared`.
        env_file=(".env.shared", ".env.private"),
        # Validation aliases above are the only way to populate fields.
        populate_by_name=False,
    )
