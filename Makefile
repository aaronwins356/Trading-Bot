.PHONY: install dev api ui test lint format dashboard data research demo docker

install:            ## Python package + dev tools, and dashboard deps
	python -m pip install -e ".[dev]"
	cd dashboard && npm ci

data:               ## free research data (no API keys): BTC 1m since 2012 + ~45 coins daily
	tradebot data free

demo:               ## replay demo bots + dashboard on http://127.0.0.1:8080
	tradebot demo

api:                ## API + built dashboard
	tradebot serve

ui:                 ## dashboard dev server (hot reload) on :5173, proxies the API on :8080
	cd dashboard && npm run dev

dashboard:          ## rebuild the dashboard bundled with the Python package
	cd dashboard && npm run build && rm -rf ../src/tradebot/api/static && cp -r dist ../src/tradebot/api/static

test:
	python -m pytest -q

lint:
	ruff check src tests && ruff format --check src tests
	cd dashboard && npx tsc --noEmit

format:
	ruff check src tests --fix && ruff format src tests

research:           ## full validation pipeline (~10 min on 4 cores) -> research/results + dashboard
	tradebot research run

docker:
	docker compose up -d --build
