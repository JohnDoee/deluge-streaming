#
# core.py
#
# Copyright (C) 2009 John Doee <johndoee@tidalstream.org>
#
# Basic plugin template created by:
# Copyright (C) 2008 Martijn Voncken <mvoncken@gmail.com>
# Copyright (C) 2007-2009 Andrew Resch <andrewresch@gmail.com>
# Copyright (C) 2009 Damien Churchill <damoxc@gmail.com>
#
# Deluge is free software.
#
# You may redistribute it and/or modify it under the terms of the
# GNU General Public License, as published by the Free Software
# Foundation; either version 3 of the License, or (at your option)
# any later version.
#
# deluge is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with deluge.    If not, write to:
# 	The Free Software Foundation, Inc.,
# 	51 Franklin Street, Fifth Floor
# 	Boston, MA  02110-1301, USA.
#
#    In addition, as a special exception, the copyright holders give
#    permission to link the code of portions of this program with the OpenSSL
#    library.
#    You must obey the GNU General Public License in all respects for all of
#    the code used other than OpenSSL. If you modify file(s) with this
#    exception, you may extend this exception to your version of the file(s),
#    but you are not obligated to do so. If you do not wish to do so, delete
#    this exception statement from your version. If you delete this exception
#    statement from all source files in the program, then also delete it here.
#

import base64
import json
import math
import os
import urllib

from twisted.internet import reactor, defer, task
from twisted.python import randbytes
from twisted.web import server, resource, static, http
from twisted.web.static import StaticProducer

import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export
from deluge.log import LOG as log
from deluge.plugins.pluginbase import CorePluginBase

from .resource import Resource

DEFAULT_PREFS = {
    'ip': '127.0.0.1',
    'port': 46123,
    'allow_remote': False,
}

from .filelike import FilelikeObjectResource

MAX_QUEUE_CHUNKS = 12

class FileServeResource(resource.Resource):
    isLeaf = True
    
    def __init__(self):
        self.file_mapping = {}
        resource.Resource.__init__(self)

    def generate_secure_token(self):
        return base64.urlsafe_b64encode(randbytes.RandomFactory().secureRandom(21, True))
    
    def add_file(self, path):
        token = self.generate_secure_token()
        self.file_mapping[token] = path
        
        return token
    
    def render_GET(self, request):
        key = request.path.split('/')[2]
        if key not in self.file_mapping:
            return resource.NoResource().render()
        
        tf = self.file_mapping[key].copy()
        tf.open()
        return FilelikeObjectResource(tf, tf.size).render_GET(request)

class AddTorrentResource(Resource):
    isLeaf = True
    
    def __init__(self, client):
        self.client = client
        Resource.__init__(self)
    
    @defer.inlineCallbacks
    def render_POST(self, request):
        torrent_data = request.args.get('torrent_data', None)
        if not torrent_data:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'missing torrent_data in request'}))
        
        torrent_data = torrent_data[0].encode('base64')
        
        torrent_id = yield self.client.add_torrent(torrent_data)
        
        if torrent_id is None:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'failed to add torrent'}))
        
        defer.returnValue(json.dumps({'status': 'success', 'infohash': torrent_id, 'message': 'torrent added successfully'}))

class StreamResource(Resource):
    isLeaf = True
    
    def __init__(self, client):
        self.client = client
        Resource.__init__(self)
    
    @defer.inlineCallbacks
    def render_GET(self, request):
        infohash = request.args.get('infohash', None)
        path = request.args.get('path', None)
        
        if infohash is None:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'missing infohash'}))
        
        if path is None:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'missing path'}))
        
        result = yield self.client.stream_torrent(infohash[0], path[0])
        defer.returnValue(json.dumps(result))

class TorrentFile(object):
    def __init__(self, torrent, file_path, size, chunk_size, offset):
        self.torrent = torrent
        self.torrent_handle = torrent.handle
        self.file_path = file_path
        self.first_chunk = offset / chunk_size
        self.last_chunk = (offset + size) / chunk_size
        self.chunk_size = chunk_size
        self.offset = offset
        self.size = size
        self.last_requested_chunk = self.first_chunk
        self.is_closed = False
        
        self.priorities_increased = {}
        
        self.first_chunk_end = self.chunk_size * (self.first_chunk + 1) - offset
    
    def open(self):
        self.update_chunk_priority()
        self.file_handler = open(self.file_path, 'rb')
    
    def get_chunk(self, tell):
        i = (tell + 1) - self.first_chunk_end
        if i <= 0:
            offset = 0
        else:
            offset = (i / self.chunk_size) + 1
        
        return self.first_chunk + offset, self.first_chunk_end + (offset * self.chunk_size)
    
    def wait_chunk_complete(self, chunk):
        d = defer.Deferred()
        
        def check_if_done():
            if self.torrent.status.pieces[chunk]:
                return d.callback(True)
            
            self.set_prio(chunk, 7)
            
            if self.is_closed:
                return d.errback(None)
            
            reactor.callLater(1.0, check_if_done)
        
        check_if_done()

        return d
    
    def set_prio(self, chunk, prio):
        if self.priorities_increased.get(chunk, 0) < prio:
            self.torrent_handle.piece_priority(chunk, prio)
            self.priorities_increased[chunk] = prio
            
            if prio == 7:
                self.torrent_handle.set_piece_deadline(chunk, 100)
    
    def prepare_torrent(self, buffer_pieces):
        self.set_prio(self.first_chunk, 7)
        self.set_prio(self.last_chunk, 7)
        
        for chunk, chunk_status in enumerate(self.torrent.status.pieces[self.first_chunk:self.first_chunk+buffer_pieces+1], self.first_chunk):
            self.set_prio(chunk, 7)
        
        self.update_chunk_priority()
    
    def is_buffered(self, expected_pieces):
        if [x for x in self.torrent.status.pieces[self.first_chunk:self.first_chunk+expected_pieces+1] if not x]:
            return False
        else:
            return True
    
    def update_chunk_priority(self): # no need to do this when the file is complete
        if self.is_closed:
            return
        
        if self.last_requested_chunk is not None:
            offset = self.last_requested_chunk + 1
            
            status_increase_count = 0
            for chunk, chunk_status in enumerate(self.torrent.status.pieces[offset:self.last_chunk+1], offset):
                if not chunk_status:
                    self.set_prio(chunk, 7)
                    status_increase_count += 1
                
                if status_increase_count > MAX_QUEUE_CHUNKS:
                    break
        
        reactor.callLater(4, self.update_chunk_priority)
    
    @defer.inlineCallbacks
    def read(self, size=1024):
        tell = self.tell()
        chunk, end_of_chunk = self.get_chunk(tell)
        self.last_requested_chunk = chunk
        print 'waiting for chunk', chunk, size, tell
        yield self.wait_chunk_complete(chunk)
        print 'done waiting', chunk, size, tell
        defer.returnValue(self.file_handler.read(min(end_of_chunk-tell, size)))
    
    def seek(self, offset, whence=os.SEEK_SET):
        return self.file_handler.seek(offset, whence)
    
    def tell(self):
        return self.file_handler.tell()
    
    def close(self):
        self.is_closed = True
        return self.file_handler.close()
    
    def copy(self):
        tf = TorrentFile(self.torrent, self.file_path, self.size, self.chunk_size, self.offset)
        tf.priorities_increased = self.priorities_increased
        
        return tf

def sleep(seconds):
    d = defer.Deferred()
    reactor.callLater(seconds, d.callback, seconds)
    return d

class Core(CorePluginBase):
    def enable(self):
        self.config = deluge.configmanager.ConfigManager("streaming.conf", DEFAULT_PREFS)
        self.fsr = FileServeResource()
        
        self.resource = Resource()
        self.resource.putChild('file', self.fsr)
        if self.config['allow_remote']:
            self.resource.putChild('add_torrent', AddTorrentResource(self))
            self.resource.putChild('stream', StreamResource(self))
        
        self.site = server.Site(self.resource)
        self.listening = reactor.listenTCP(self.config.config['port'], self.site, interface=self.config.config['ip'])

    @defer.inlineCallbacks
    def disable(self):
        self.site.stopFactory()
        yield self.listening.stopListening()

    def update(self):
        pass

    @export
    @defer.inlineCallbacks
    def set_config(self, config):
        """Sets the config dictionary"""
        do_reload = False
        for key in config.keys():
            self.config[key] = config[key]
        self.config.save()
        
        yield self.disable()
        self.enable()

    @export
    def get_config(self):
        """Returns the config dictionary"""
        return self.config.config
    
    @export
    @defer.inlineCallbacks
    def add_torrent(self, torrent_data):
        core = component.get("Core")
        tid = yield core.add_torrent_file('file.torrent', torrent_data, {'add_paused': True})
        
        tor = component.get("TorrentManager").torrents.get(tid, None)
        
        state = tor.get_status(['files'])
        tor.set_file_priorities([0] * len(state['files']))
        
        defer.returnValue(tid)
    
    @export
    @defer.inlineCallbacks
    def stream_torrent(self, tid, filepath):
        tor = component.get("TorrentManager").torrents.get(tid, None)
        
        if tor is None: # torrent isn't downloaded yet
            defer.returnValue({'status': 'error', 'message': 'torrent_not_found'})
        
        status = tor.get_status(['piece_length', 'files', 'file_priorities', 'file_progress', 'state', 'save_path'])
        pieces = tor.status.pieces
        piece_length = status['piece_length']
        files = status['files']
        
        for f, priority, progress in zip(files, status['file_priorities'], status['file_progress']):
            f['first_piece'] = f['offset'] / piece_length
            f['last_piece'] = (f['offset'] + f['size']) / piece_length
            f['pieces'] = pieces[f['first_piece']:f['last_piece']+1]
            f['priority'] = priority
            f['progress'] = progress
        
        f = [f for f in files if f['path'] == filepath]
        
        if not f: # file not found in torrent
            defer.returnValue({'status': 'error', 'message': 'file_not_found'})
        f = f[0]
        
        priorities = [0] * len(tor.get_status(['files'])['files'])
        priorities[f['index']] = 1
        tor.set_file_priorities(priorities)
        
        tor.resume()
        
        EXPECTED_PERCENT = 5.0
        EXPECTED_SIZE = 5*1024*1024
        
        percent_pieces = int(math.ceil((len(f['pieces']) / 100.0) * EXPECTED_PERCENT))
        size_pieces = int(min(math.ceil((EXPECTED_SIZE * 1.0) / piece_length), f['pieces']))
        expected_pieces = max(percent_pieces, size_pieces)
        
        fp = os.path.join(status['save_path'], f['path'])
        
        tf = TorrentFile(tor, fp, f['size'], status['piece_length'], f['offset'])
        tf.prepare_torrent(expected_pieces)
        
        for _ in range(300):
            if os.path.isfile(fp) and tf.is_buffered(expected_pieces):
                break
            
            yield sleep(1)
        
        defer.returnValue({
            'status': 'success',
            'url': 'http://%s:%s/file/%s/%s' % (self.config.config['ip'], self.config.config['port'],
                                           self.fsr.add_file(tf),
                                           urllib.quote(f['path'].split('/')[-1]))
        })