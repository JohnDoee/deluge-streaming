from twisted.internet import defer
from twisted.python import log
from twisted.web import http, resource, server, static

#  NOTICE!
# All these producers are taken directly from the Twisted Project.
# This is because i needed to make them accept defers.
# /NOTICE!

class NoRangeStaticProducer(static.NoRangeStaticProducer):
    @defer.inlineCallbacks
    def resumeProducing(self):
        if not self.request:
            return
        
        data = yield defer.maybeDeferred(self.fileObject.read, self.bufferSize)
        
        if not self.request:
            return
        
        if data:
            # this .write will spin the reactor, calling .doWrite and then
            # .resumeProducing again, so be prepared for a re-entrant call
            self.request.write(data)
        else:
            self.request.unregisterProducer()
            self.request.finish()
            self.stopProducing()

class SingleRangeStaticProducer(static.SingleRangeStaticProducer):
    @defer.inlineCallbacks
    def resumeProducing(self):
        if not self.request:
            return
        
        data = yield defer.maybeDeferred(self.fileObject.read,
            min(self.bufferSize, self.size - self.bytesWritten))
        
        if not self.request:
            return
        
        if data:
            self.bytesWritten += len(data)
            # this .write will spin the reactor, calling .doWrite and then
            # .resumeProducing again, so be prepared for a re-entrant call
            self.request.write(data)
        
        if self.request and self.bytesWritten == self.size:
            self.request.unregisterProducer()
            self.request.finish()
            self.stopProducing()

class MultipleRangeStaticProducer(static.MultipleRangeStaticProducer):
    @defer.inlineCallbacks
    def resumeProducing(self):
        if not self.request:
            return
        
        data = []
        dataLength = 0
        done = False
        while dataLength < self.bufferSize:
            if self.partBoundary:
                dataLength += len(self.partBoundary)
                data.append(self.partBoundary)
                self.partBoundary = None
            p = yield defer.maybeDeferred(self.fileObject.read,
                min(self.bufferSize - dataLength,
                    self._partSize - self._partBytesWritten))
            self._partBytesWritten += len(p)
            dataLength += len(p)
            data.append(p)
            if self.request and self._partBytesWritten == self._partSize:
                try:
                    self._nextRange()
                except StopIteration:
                    done = True
                    break
        
        if not self.request:
            return
        
        self.request.write(''.join(data))
        
        if done:
            self.request.unregisterProducer()
            self.request.finish()
            self.stopProducing()
            self.request = None

class FilelikeObjectResource(static.File):
    isLeaf = True
    contentType = None
    fileObject = None
    encoding = 'bytes'

    def __init__(self, fileObject, size, contentType='bytes'):
        self.contentType = contentType
        self.fileObject = fileObject
        self.fileSize = size
        resource.Resource.__init__(self)

    def _setContentHeaders(self, request, size=None):
        if size is None:
            size = self.getFileSize()

        if size:
            request.setHeader('content-length', str(size))
        if self.contentType:
            request.setHeader('content-type', self.contentType)
        if self.encoding:
            request.setHeader('content-encoding', self.encoding)

    def makeProducer(self, request, fileForReading):
        """
        Make a L{StaticProducer} that will produce the body of this response.

        This method will also set the response code and Content-* headers.

        @param request: The L{Request} object.
        @param fileForReading: The file object containing the resource.
        @return: A L{StaticProducer}.  Calling C{.start()} on this will begin
            producing the response.
        """
        byteRange = request.getHeader('range')
        if byteRange is None or not self.getFileSize():
            self._setContentHeaders(request)
            request.setResponseCode(http.OK)
            return NoRangeStaticProducer(request, fileForReading)
        try:
            parsedRanges = self._parseRangeHeader(byteRange)
        except ValueError:
            log.msg("Ignoring malformed Range header %r" % (byteRange,))
            self._setContentHeaders(request)
            request.setResponseCode(http.OK)
            return NoRangeStaticProducer(request, fileForReading)

        if len(parsedRanges) == 1:
            offset, size = self._doSingleRangeRequest(
                request, parsedRanges[0])
            self._setContentHeaders(request, size)
            return SingleRangeStaticProducer(
                request, fileForReading, offset, size)
        else:
            rangeInfo = self._doMultipleRangeRequest(request, parsedRanges)
            return MultipleRangeStaticProducer(
                request, fileForReading, rangeInfo)

    def getFileSize(self):
        return self.fileSize

    def render_GET(self, request):
        """
        Begin sending the contents of this L{File} (or a subset of the
        contents, based on the 'range' header) to the given request.
        """
        request.setHeader('accept-ranges', 'bytes')

        producer = self.makeProducer(request, self.fileObject)

        if request.method == 'HEAD':
            self.fileObject.close()
            return ''

        producer.start()
        # and make sure the connection doesn't get closed
        return server.NOT_DONE_YET
    render_HEAD = render_GET
