virtualenv .env-egg
.env-egg/bin/pip install -U thomas
ln -s .env-egg/lib/python2.7/site-packages/thomas .
ln -s .env-egg/lib/python2.7/site-packages/rarfile.py .
ln -s .env-egg/lib/python2.7/site-packages/six.py .
ln -s .env-egg/lib/python2.7/site-packages/rfc6266.py .
ln -s .env-egg/lib/python2.7/site-packages/lepl .
ln -s .env-egg/lib/python2.7/site-packages/pytz .
.env-egg/bin/python setup.py bdist_egg