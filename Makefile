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

bandit:
	bandit --configfile bandit.yaml -r ./src

tests: check_types static_code_analysis bandit unit_tests

start_server:
	cd ./src; gunicorn -b 0.0.0.0:8080 webserver:app