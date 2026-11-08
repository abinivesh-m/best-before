.PHONY: install install-dev run test bifrost docker

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

run:
	python -m uvicorn app:app --host 127.0.0.1 --port 8000

test:
	python -m pytest -q

# Starts the real Bifrost gateway on :8080 with the Groq provider.
bifrost:
	npx -y @maximhq/bifrost -app-dir .bifrost -port 8080

docker:
	docker compose up --build
