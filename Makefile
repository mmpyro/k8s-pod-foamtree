restore:
	uv pip install -e .

restore_dev:
	uv pip install -e '.[dev]'

restore_ci:
	uv pip install --system -e '.[dev]'

node_modules: package.json package-lock.json
	npm ci

web: node_modules
	npm run build

run: web
	uv run k8sfoams

run-synthetic: web
	uv run k8sfoams --synthetic

unit_tests:
	uv run pytest -v --junit-xml=test-results.xml

unit_tests_ci:
	pytest -v --junit-xml=test-results.xml

static_code_analysis:
	uv run flake8 ./k8sfoam

static_code_analysis_ci:
	flake8 ./k8sfoam

check_types:
	uv run mypy ./k8sfoam

check_types_ci:
	mypy ./k8sfoam

bandit:
	uv run bandit --configfile bandit.yaml -r ./k8sfoam

bandit_ci:
	bandit --configfile bandit.yaml -r ./k8sfoam

tests: check_types static_code_analysis bandit unit_tests

tests_ci: check_types_ci static_code_analysis_ci bandit_ci unit_tests_ci

build: web
	uv build

# The live demo the docs embed: the UI in static mode plus the synthetic clusters' data.
docs-demo: web
	uv run python -m k8sfoam.src.synthetic.export docs/demo

docs_demo_ci: web
	python -m k8sfoam.src.synthetic.export docs/demo

.venv-docs: docs/requirements.txt
	python3 -m venv .venv-docs
	.venv-docs/bin/pip install -q -r docs/requirements.txt
	touch .venv-docs

docs-build: .venv-docs docs-demo
	.venv-docs/bin/mkdocs build --strict

docs-serve: .venv-docs docs-demo
	.venv-docs/bin/mkdocs serve

clean:
	rm -rf dist *.egg-info build site docs/demo k8sfoam/src/frontend/bundle.js
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
