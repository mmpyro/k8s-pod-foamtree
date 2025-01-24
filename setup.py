from setuptools import setup, find_packages
setup(
    name='k8sfoams',
    version='1.1.2',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/mmpyro/k8s-pod-foamtree',

    description='K8s pod foamtree visualizer',
    author="Michal Marszalek",
    author_email="mmpyro@gmail.com",
    license='Apache 2.0',

    scripts=['k8sfoams.py'],
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,

    install_requires=[
      "flask==3.1.0",
      "kubernetes==31.0.0",
      "pydash==8.0.4",
      "bitmath==1.3.3.1",
      "requests==2.32.3"
    ],
    extras_require={
      # Development dependencies
      "dev": [
        "pytest==8.3.4",
        "flake8==7.1.1",
        "mypy==1.14.1",
        "mock==5.1.0",
        "bandit==1.8.0"
      ]
    },
    classifiers=[
    'Development Status :: 3 - Alpha',
    'Topic :: Scientific/Engineering :: Visualization',
    'License :: OSI Approved :: Apache Software License',
    'Programming Language :: Python :: 3.8',
  ],
  keywords = ['foamtree', 'k8s', 'visualization']
)
