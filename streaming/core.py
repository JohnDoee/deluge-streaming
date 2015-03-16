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
import logging
import math
import os
import urllib

from collections import defaultdict
from datetime import datetime, timedelta

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

logger = logging.getLogger(__name__)

DEFAULT_PREFS = {
    'ip': '127.0.0.1',
    'port': 46123,
    'allow_remote': False,
    'reset_complete': True,
    'remote_username': 'username',
    'remote_password': 'password',
}

from .filelike import FilelikeObjectResource

MIN_QUEUE_CHUNKS = 6
EXPECTED_PERCENT = 0.3
EXPECTED_SIZE = 5*1024*1024
HANDLERS_TIMEOUT = timedelta(hours=12)

class UnknownTorrentException(Exception):
    pass

class UnknownFileException(Exception):
    pass

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
            return resource.NoResource().render(request)
        
        tf = self.file_mapping[key].copy()
        tf.open()
        return FilelikeObjectResource(tf, tf.size).render_GET(request)

class AddTorrentResource(Resource):
    isLeaf = True
    
    def __init__(self, client, *args, **kwargs):
        self.client = client
        Resource.__init__(self, *args, **kwargs)
    
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
    
    def __init__(self, client, *args, **kwargs):
        self.client = client
        Resource.__init__(self, *args, **kwargs)
    
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
    file_handler = None
    def __init__(self, torrent_handler, file_path, torrent_file_path, size, chunk_size, offset, file_index):
        self.torrent_handler = torrent_handler
        self.file_path = file_path
        self.torrent_file_path = torrent_file_path
        self.first_chunk = offset / chunk_size
        self.last_chunk = (offset + size) / chunk_size
        self.chunk_size = chunk_size
        self.offset = offset
        self.file_index = file_index
        self.size = size
        self.last_requested_chunk = self.first_chunk
        self.is_closed = False
        self.is_active = False
        self.last_activity = datetime.now()
        self.end_chunks = []
        
        self.first_chunk_end = self.chunk_size * (self.first_chunk + 1) - offset
        
        self.torrent_handler.torrent_files[torrent_file_path].append(self)
    
    def open(self):
        if self.is_closed:
            raise IOError('Unable to reopen file')
        
        self.file_handler = open(self.file_path, 'rb')
    
    def get_chunk(self, tell):
        i = (tell + 1) - self.first_chunk_end
        if i <= 0:
            offset = 0
        else:
            offset = (i / self.chunk_size) + 1
        
        return self.first_chunk + offset, self.first_chunk_end + (offset * self.chunk_size)
    
    def prepare_torrent(self, buffer_pieces):
        self.torrent_handler.schedule_chunk(self.first_chunk, 0)
        self.torrent_handler.schedule_chunk(self.last_chunk, 0)
        
        self.end_chunks.append(self.last_chunk)
        
        if self.first_chunk != self.last_chunk:
            self.torrent_handler.schedule_chunk(self.last_chunk-1, 0)
            self.end_chunks.append(self.last_chunk-1)
        
        for chunk, chunk_status in enumerate(self.torrent_handler.torrent.status.pieces[self.first_chunk:min(self.first_chunk+buffer_pieces, self.last_chunk)+1], self.first_chunk):
            self.torrent_handler.schedule_chunk(chunk, chunk-self.first_chunk)
    
    @defer.inlineCallbacks
    def read(self, size=1024):
        self.is_active = True
        self.last_activity = datetime.now()
        
        tell = self.tell()
        chunk, end_of_chunk = self.get_chunk(tell)
        self.last_requested_chunk = chunk
        
        logger.debug('waiting for chunk %s, %s, %s' % (chunk, size, tell))
        yield self.wait_chunk_complete(chunk)
        logger.debug('done waiting %s, %s, %s' % (chunk, size, tell))
        
        defer.returnValue(self.file_handler.read(min(end_of_chunk-tell, size)))
    
    def wait_chunk_complete(self, chunk):
        d = defer.Deferred()
        
        def check_if_done():
            if self.torrent_handler.torrent.status.pieces[chunk]:
                return d.callback(True)
            
            self.torrent_handler.schedule_chunk(chunk, 0)
            
            if self.is_closed:
                logger.debug('The file closed, shutting down torrent')
                return
            
            reactor.callLater(1.0, check_if_done)
        
        check_if_done()

        return d
    
    def seek(self, offset, whence=os.SEEK_SET):
        return self.file_handler.seek(offset, whence)
    
    def tell(self):
        return self.file_handler.tell()
    
    def close(self):
        if not self.is_closed and self in self.torrent_handler.torrent_files[self.torrent_file_path]:
            self.torrent_handler.torrent_files[self.torrent_file_path].remove(self)
        self.is_closed = True
        
        if self.file_handler:
            return self.file_handler.close()
    
    def copy(self):
        tf = TorrentFile(self.torrent_handler, self.file_path, self.torrent_file_path,
                         self.size, self.chunk_size, self.offset, self.file_index)
        return tf

class TorrentHandler(object):
    def __init__(self, torrent, torrent_id, core):
        self.torrent = torrent
        self.torrent_handle = torrent.handle
        self.torrent_id = torrent_id
        self.core = core
        self.priorities = [0] * len(torrent.get_status(['files'])['files'])
        self.torrent_files = defaultdict(list)
        self.priorities_increased = {}
        # need to blackhole all pieces not downloaded yet
        self.last_activity = datetime.now()
        self.update_chunk_priorities()
        
    
    def is_buffered(self, first_chunk, last_chunk):
        buffer_status = [x for x in self.torrent.status.pieces[first_chunk:last_chunk+1] if not x]
        logger.debug('Current buffer status for file (%s): %r' % (first_chunk, buffer_status))
        if buffer_status:
            return False
        else:
            return True
    
    def update_priorities(self):
        self.torrent.set_file_priorities(self.priorities)
    
    def schedule_chunk(self, chunk, distance):
        if chunk not in self.priorities_increased:
            logger.debug('Scheduled chunk %s at distance %s' % (chunk, distance))
            
            self.torrent_handle.piece_priority(chunk, (7 if distance <= 4 else 6))
            self.torrent_handle.set_piece_deadline(chunk, 700*(distance+1))
            
            self.priorities_increased[chunk] = True
    
    def update_chunk_priority(self, tfs):
        handled_heads = []
        for tf in sorted(tfs, key=lambda x:x.last_requested_chunk, reverse=True):
            if datetime.now() - tf.last_activity > HANDLERS_TIMEOUT:
                logger.debug('Torrentfile timed out')
                tf.close()
                continue
            
            if tf.last_requested_chunk is not None:
                if handled_heads:
                    if tf.last_requested_chunk in handled_heads:
                        continue
                    
                    already_queued = False
                    for i in sorted(handled_heads):
                        if i > tf.last_requested_chunk:
                            if 0 not in self.torrent.status.pieces[tf.last_requested_chunk:i]:
                                already_queued = True
                            break
                    if already_queued:
                        continue
                
                offset = tf.last_requested_chunk + 1
                
                current_buffer_offset = 5
                for chunk in tf.end_chunks:
                    if not self.torrent.status.pieces[chunk]:
                        logger.info('End chunks are not downloaded, setting buffer offset differently')
                        current_buffer_offset = 20
                        break
                
                status_increase_count = 0
                current_buffer = 0
                found_buffer_end = False
                for chunk, chunk_status in enumerate(self.torrent.status.pieces[offset:tf.last_chunk+1], offset):
                    if not chunk_status:
                        if not found_buffer_end:
                            logger.debug('Found buffer end at %s, have a buffer of %s' % (chunk, current_buffer))
                        found_buffer_end = True
                        
                        if status_increase_count <= MIN_QUEUE_CHUNKS:
                            self.schedule_chunk(chunk, chunk-offset)
                        elif self.torrent_handle.piece_priority(chunk) == 0:
                            self.torrent_handle.piece_priority(chunk, 1)
                        
                        status_increase_count += 1
                    elif not found_buffer_end:
                        current_buffer += 1
                    
                    if status_increase_count >= max(MIN_QUEUE_CHUNKS, current_buffer-current_buffer_offset):
                        break
                
                handled_heads.append(tf.last_requested_chunk)
        
        return bool(handled_heads)
        
    def update_chunk_priorities(self): # TODO: check if torrent still exists
        file_progress = self.torrent.get_status(['file_progress'])['file_progress']
        incomplete_files = False
        
        for torrent_file_path, tfs in self.torrent_files.items():
            if not tfs:
                logger.debug('No heads left for %r' % torrent_file_path)
                del self.torrent_files[torrent_file_path]
                continue
            
            tf = tfs[0]
            if file_progress[tf.file_index] == 1.0:
                logger.info('%s is already complete, skipping' % torrent_file_path)
                continue
            
            if self.update_chunk_priority(tfs):
                self.last_activity = datetime.now()
            
            incomplete_files = True
        
        if not incomplete_files and self.core.config['reset_complete'] and not all(self.priorities):
            logger.info('We are not doing any file streamings, but not downloading all files, changing that.')
            self.priorities = [1] * len(self.priorities)
            self.update_priorities()
        
        if datetime.now() - self.last_activity > HANDLERS_TIMEOUT:
            logger.debug('Torrent handler idle, killing myself.')
            if self.torrent_id in self.core.torrent_handlers:
                del self.core.torrent_handlers[self.torrent_id]
            return
        
        reactor.callLater(2, self.update_chunk_priorities)
    
    def blackhole_all_pieces(self, first_chunk, last_chunk):
        for chunk in range(first_chunk, last_chunk+1):
            self.torrent_handle.piece_priority(chunk, 0)
    
    @defer.inlineCallbacks
    def get_file(self, filepath):
        status = self.torrent.get_status(['piece_length', 'files', 'file_priorities', 'file_progress', 'state', 'save_path'])
        pieces = self.torrent.status.pieces
        piece_length = status['piece_length']
        files = status['files']
        
        for f, priority, progress in zip(files, status['file_priorities'], status['file_progress']):
            if f['path'] == filepath:
                f['first_piece'] = f['offset'] / piece_length
                f['last_piece'] = (f['offset'] + f['size']) / piece_length
                f['pieces'] = pieces[f['first_piece']:f['last_piece']+1]
                f['priority'] = priority
                f['progress'] = progress
                
                break
        else:
            raise UnknownFileException()
        
        fp = os.path.join(status['save_path'], f['path'])
        
        if progress == 1: # file is complete, no need to fire up all the torrent jazz
            defer.returnValue(static.File(fp))
        
        self.priorities = [0] * len(self.priorities)
        self.priorities[f['index']] = 3
        current_tfs = []
        for tfs in self.torrent_files.values():
            if not tfs:
                continue
            tf = tfs[0]
            self.priorities[tf.file_index] = 3
            current_tfs.append((tf, self.torrent_handle.piece_priorities()[tf.first_chunk:tf.last_chunk+1]))
        
        self.update_priorities()
        
        for tf, chunk_status in current_tfs:
            logger.info('Setting chunks to old status')
            for i, chunk in enumerate(chunk_status, tf.first_chunk):
                logger.debug('Setting status on chunk %s back to %s' % (i, chunk))
                self.torrent_handle.piece_priority(i, chunk)

        self.torrent.resume()
        
        percent_pieces = int(math.ceil((len(f['pieces']) / 100.0) * EXPECTED_PERCENT))
        size_pieces = int(min(math.ceil((EXPECTED_SIZE * 1.0) / piece_length), f['pieces']))
        expected_pieces = max(percent_pieces, size_pieces) # we need to download either 5% or 5MB of the file before allowing stream.
        
        tf = TorrentFile(self, fp, f['path'], f['size'], status['piece_length'], f['offset'], f['index'])
        
        if len(self.torrent_files[f['path']]) == 1:
            self.blackhole_all_pieces(tf.first_chunk, tf.last_chunk)
        
        tf.prepare_torrent(expected_pieces)
        
        for _ in range(300):
            if os.path.isfile(fp) and self.is_buffered(tf.first_chunk, tf.first_chunk+expected_pieces) and self.is_buffered(tf.last_chunk, tf.last_chunk):
                break
            
            yield sleep(1)
        
        defer.returnValue(tf)

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
            self.resource.putChild('add_torrent', AddTorrentResource(username=self.config['remote_username'],
                                                                     password=self.config['remote_password'],
                                                                     client=self))
            self.resource.putChild('stream', StreamResource(username=self.config['remote_username'],
                                                            password=self.config['remote_password'],
                                                            client=self))
        
        self.site = server.Site(self.resource)
        
        session = component.get("Core").session
        settings = session.get_settings()
        settings['prioritize_partial_pieces'] = True
        session.set_settings(settings)
        
        self.torrent_handlers = {}
        
        try:
            self.listening = reactor.listenTCP(self.config['port'], self.site, interface=self.config['ip'])
        except:
            self.listening = reactor.listenTCP(self.config['port'], self.site, interface='127.0.0.1')

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
    
    def get_torrent_handler(self, tid):
        if tid not in self.torrent_handlers:
            tor = component.get("TorrentManager").torrents.get(tid, None)
            
            if tor is None:
                raise UnknownTorrentException()
            
            self.torrent_handlers[tid] = TorrentHandler(tor, tid, self)
        
        return self.torrent_handlers[tid]
    
    @export
    @defer.inlineCallbacks
    def stream_torrent(self, tid, filepath):
        try:
            torrent_handler = yield self.get_torrent_handler(tid)
        except UnknownTorrentException: # torrent isn't added yet
            defer.returnValue({'status': 'error', 'message': 'torrent_not_found'})
        
        try:
            tf = yield torrent_handler.get_file(filepath)
        except UnknownFileException:
            defer.returnValue({'status': 'error', 'message': 'file_not_found'})
        
        defer.returnValue({
            'status': 'success',
            'url': 'http://%s:%s/file/%s/%s' % (self.config.config['ip'], self.config.config['port'],
                                           self.fsr.add_file(tf), urllib.quote_plus(os.path.basename(tf.file_path)))
        })