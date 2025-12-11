restore:
	uv pip install -e .

restore_dev:
	uv pip install -e '.[dev]'

run:
	uv run k8sfoams

unit_tests:
	uv run pytest -v --junit-xml=test-results.xml

static_code_analysis:
	uv run flake8 ./k8sfoam

check_types:
	uv run mypy ./k8sfoam

bandit:
	uv run bandit --configfile bandit.yaml -r ./k8sfoam

tests: check_types static_code_analysis bandit unit_tests

build:
	uv build

clean:
	rm -rf dist *.egg-info build
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
