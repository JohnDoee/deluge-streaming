import base64

from twisted.web.resource import Resource as TwistedResource, _computeAllowedMethods
from twisted.web import server
from twisted.internet import defer


class Resource(TwistedResource):
    content_type = 'application/json'

    def __init__(self, username=None, password=None, *args, **kwargs):
        self.username = username
        self.password = password
        TwistedResource.__init__(self, *args, **kwargs)

    def render(self, request):  # Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==
        """
        Adds support for deferred render methods
        """
        auth_header = request.getHeader('Authorization')

        if self.username or self.password:
            authenticated = False
            if auth_header:
                auth_header = auth_header.split(' ')
                if len(auth_header) > 1 and auth_header[0] == 'Basic':
                    userpass = base64.b64decode(auth_header[1].encode('utf-8')).decode('utf-8').split(':')
                    if len(userpass) == 2:
                        username, password = userpass
                        if self.username == username and self.password == password:
                            authenticated = True

            if not authenticated:
                request.setResponseCode(401)
                return 'Unauthorized'

        m = getattr(self, 'render_' + request.method.decode('utf-8'), None)
        if not m:
            # This needs to be here until the deprecated subclasses of the
            # below three error resources in twisted.web.error are removed.
            from twisted.web.error import UnsupportedMethod
            allowedMethods = (getattr(self, 'allowedMethods', 0) or
                              _computeAllowedMethods(self))
            raise UnsupportedMethod(allowedMethods)

        result = defer.maybeDeferred(m, request)

        def write_rest(defer_result, request):
            request.write(defer_result)
            request.finish()

        def err_rest(defer_result=None):
            defer_result.printTraceback()
            request.finish()

        result.addCallback(write_rest, request)
        result.addErrback(err_rest)

        return server.NOT_DONE_YET
