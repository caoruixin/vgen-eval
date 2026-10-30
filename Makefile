.PHONY: setup web start mock dev test probe probe-list models gen-assets

setup:            ## install Python + web deps and build the UI
	uv sync
	pnpm -C web install
	pnpm -C web build

web:              ## rebuild the UI
	pnpm -C web build

start:            ## run the app against tokenbay.com (real spending) on http://127.0.0.1:8765
	uv run vgen serve

mock:             ## fake gateway, no spending, separate data-mock/ dir → http://127.0.0.1:8766
	uv run vgen serve --mock --port 8766

dev:              ## backend + Vite dev server with hot reload (http://127.0.0.1:5173)
	(uv run vgen serve & pnpm -C web dev; kill %1)

test:             ## unit + end-to-end tests (mock gateway, no network)
	uv run pytest -q

probe-list:       ## list live probe cases
	uv run vgen probe --list

probe:            ## paid capability probe at the cheapest settings (asks before spending)
	uv run vgen probe --budget 10

models:           ## check which registered models are live on the gateway
	uv run vgen models

gen-assets:       ## generate scenario images marked `generated: true` (a few cents)
	uv run vgen gen-assets
