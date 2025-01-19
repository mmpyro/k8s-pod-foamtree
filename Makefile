input='requirements.in'
output='requirements.txt'
record='record.txt'

compile:
	pip-compile $(input) -o $(output)

restore:
	pip install -r $(output)

unit_tests:
	pytest -v

static_code_analysis:
	flake8 ./k8sfoam

check_types:
	mypy ./k8sfoam

bandit:
	bandit --configfile bandit.yaml -r ./k8sfoam

tests: check_types static_code_analysis bandit unit_tests

# start_server:
# 	gunicorn -b 0.0.0.0:8080 webserver:app

setup:
	python ./setup.py sdist

build:
	python ./setup.py build

install:
	python ./setup.py install --record $(record)

clean:
	python setup.py clean --all

reinstall: clean build install

uninstall: clean
	pip uninstall k8sfoams