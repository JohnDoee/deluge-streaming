python3 -m venv .env-egg
.env-egg/bin/pip install -U thomas
ln -s .env-egg/lib/python*/site-packages/thomas .
ln -s .env-egg/lib/python*/site-packages/rarfile.py .
ln -s .env-egg/lib/python*/site-packages/six.py .
ln -s .env-egg/lib/python*/site-packages/rfc6266.py .
ln -s .env-egg/lib/python*/site-packages/lepl .
ln -s .env-egg/lib/python*/site-packages/pytz .
.env-egg/bin/python setup.py bdist_egg
