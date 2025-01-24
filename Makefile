input='requirements.in'
output='requirements.txt'
record='record.txt'

restore:
	python setup.py install

restore_dev:
	pip install 'k8sfoams[dev]'

unit_tests:
	pytest -v --junit-xml=test-results.xml

static_code_analysis:
	flake8 ./k8sfoam

check_types:
	mypy ./k8sfoam

bandit:
	bandit --configfile bandit.yaml -r ./k8sfoam

tests: check_types static_code_analysis bandit unit_tests

build:
	python ./setup.py sdist bdist_wheel

install:
	python ./setup.py install --record $(record)

clean:
	python setup.py clean --all

reinstall: clean build install

uninstall: clean
	pip uninstall k8sfoams