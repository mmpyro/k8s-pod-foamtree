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

build:
	python3 ./setup.py build

install:
	python3 ./setup.py install --record $(record)

clean:
	rm -rf ./build ./dist ./k8s_pod_foamtree.egg-info; xargs rm -rf < $(record); rm $(record); touch $(record)

reinstall: clean build install