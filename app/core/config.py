from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+psycopg://rpg:rpg@db:5432/rpgkb"
    # Ollama runs on the host (outside docker). In compose, set to http://host.docker.internal:11434
    ollama_base_url: str = "http://host.docker.internal:11434"

    app_env: str = "dev"

settings = Settings()
