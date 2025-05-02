rm -rf dist
python setup.py sdist
twine upload dist/*
pip uninstall -y nceu
echo "Waiting for 60 seconds before installing the new version"
sleep 60
pip install nceu