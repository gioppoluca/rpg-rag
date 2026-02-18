

docker compose up -d
docker compose logs -f pg

psql "postgresql://rag:ragpass@localhost:5432/ragkb" -c "SELECT extname FROM pg_extension ORDER BY 1;"


