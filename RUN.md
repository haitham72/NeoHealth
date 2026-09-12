# ReguLense — how to run

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and add OPENAI_API_KEY
docker compose up --build
```

Frontend: http://localhost:8080 | Backend: http://localhost:8000

## Manual setup

```bash
docker start temporal_note-db
source .venv/bin/activate
cd backend && python -m app.main  # Terminal 1
cd ..
cd frontend && npm run dev         # Terminal 2
```

Frontend: http://localhost:5173
