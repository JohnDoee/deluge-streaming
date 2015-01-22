from twisted.web.resource import Resource as TwistedResource, _computeAllowedMethods
from twisted.web import server
from twisted.internet import defer


class Resource(TwistedResource):
    content_type = 'application/json'
    

    def render(self, request):
        """
        Adds support for deferred render methods
        """
        m = getattr(self, 'render_' + request.method, None)
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