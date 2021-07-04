input='requirements.in'
output='requirements.txt'

compile:
	pip-compile $(input) -o $(output)

restore:
	pip install -r $(output)

unit_tests:
	pytest -v

static_code_analysis:
	flake8 ./src

check_types:
	mypy ./src

tests: check_types static_code_analysis unit_tests
