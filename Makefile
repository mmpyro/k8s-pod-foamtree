restore:
	uv pip install -e .

restore_dev:
	uv pip install -e '.[dev]'

restore_ci:
	uv pip install --system -e '.[dev]'

run:
	uv run k8sfoams

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

build:
	uv build

clean:
	rm -rf dist *.egg-info build
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
