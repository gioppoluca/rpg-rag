

docker compose up -d
docker compose logs -f pg

psql "postgresql://rag:ragpass@localhost:5432/ragkb" -c "SELECT extname FROM pg_extension ORDER BY 1;"


Dev workflow

docker compose -f docker-compose.dev.yml up -d

Add deps without rebuilding images:

docker compose -f docker-compose.dev.yml exec api bash

pip install <package> (it persists in the venv volume)

Restart only the service: docker compose -f docker-compose.dev.yml restart api