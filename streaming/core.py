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
import os
import urllib

import deluge.configmanager

from collections import defaultdict

from deluge import component
from deluge._libtorrent import lt
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase

from twisted.internet import reactor, defer, task
from twisted.python import randbytes
from twisted.web import server, resource, static, http, client

from .filelike import FilelikeObjectResource
from .resource import Resource

logger = logging.getLogger(__name__)

DEFAULT_PREFS = {
    'ip': '127.0.0.1',
    'port': 46123,
    'allow_remote': False,
    'reset_complete': True,
    'use_stream_urls': True,
    'auto_open_stream_urls': False,
    'remote_username': 'username',
    'remote_password': 'password',
}


def sleep(seconds):
    d = defer.Deferred()
    reactor.callLater(seconds, d.callback, seconds)
    return d

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
        
        f = self.file_mapping[key]
        if f.is_complete():
            return static.File(f.full_path).render_GET(request)
        else:
            tfr = f.open()
            return FilelikeObjectResource(tfr, f.size).render_GET(request)

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

class UnknownTorrentException(Exception):
    pass

class UnknownFileException(Exception):
    pass

class TorrentFileReader(object):
    def __init__(self, torrent_file):
        self.torrent_file = torrent_file
        self.size = torrent_file.size
        self.position = 0
        
        self.waiting_for_piece = None
        self.current_piece = None
        self.current_piece_data = None
    
    @defer.inlineCallbacks
    def read(self, size=1024):
        required_piece, read_position = self.torrent_file.get_piece_info(self.position)
        
        if self.current_piece != required_piece:
            logger.debug('We are missing piece %i and it is required, requesting' % (required_piece, ))
            self.waiting_for_piece = required_piece
            self.current_piece_data = yield self.torrent_file.get_piece_data(required_piece)
            self.current_piece = required_piece
            self.waiting_for_piece = None
        
        logger.debug('We can read from local piece from %s size %s from position %s' % (read_position, size, self.position))
        data = self.current_piece_data[read_position:read_position+size]
        self.position += len(data)
        
        defer.returnValue(data)
    
    def tell(self):
        return self.position
    
    def close(self):
        self.torrent_file.close(self)
    
    def seek(self, offset, whence=os.SEEK_SET):
        self.position = offset

class TorrentFile(object): # can be read from, knows about itself
    def __init__(self, torrent, first_piece, last_piece, piece_size, offset, path, full_path, size, index):
        self.torrent = torrent
        self.first_piece = first_piece
        self.last_piece = last_piece
        self.piece_size = piece_size
        self.offset = offset
        self.path = path
        self.size = size
        self.full_path = full_path
        self.index = index
        
        self.file_requested = False
        self.do_shutdown = False
        self.first_piece_end = self.piece_size * (self.first_piece + 1) - offset
        self.waiting_pieces = {}
        self.current_readers = []
        self.registered_alert = False
        
        self.alerts = component.get("AlertManager")
        
    
    def open(self):
        """
        Returns a filelike object
        """
        if not self.registered_alert:
            self.alerts.register_handler("read_piece_alert", self.on_alert_got_piece_data)
            self.registered_alert = True
        tfr = TorrentFileReader(self)
        self.current_readers.append(tfr)
        self.file_requested = False
        return tfr
    
    def close(self, tfr):
        self.current_readers.remove(tfr)
        self.torrent.unprioritize_pieces(tfr)
    
    def is_complete(self):
        torrent_status = self.torrent.torrent.get_status(['file_progress', 'state'])
        file_progress = torrent_status['file_progress']
        return file_progress[self.index] == 1.0
    
    def get_piece_info(self, tell):
        return divmod((self.offset + tell), self.piece_size)
    
    def on_alert_got_piece_data(self, alert):
        torrent_id = str(alert.handle.info_hash())
        if torrent_id != self.torrent.infohash:
            return
        
        logger.debug('Got piece data for piece %s' % alert.piece)
        if alert.piece not in self.waiting_pieces:
            logger.debug('Got data for piece %i, but no data needed for this piece?' % alert.piece)
            return
        # TODO: check piece size is not zero
        
        self.waiting_pieces[alert.piece].callback(alert.buffer)
    
    @defer.inlineCallbacks
    def get_piece_data(self, piece):
        logger.debug('Trying to get piece data for piece %s' % piece)
        for reader in self.current_readers:
            if reader.current_piece == piece:
                defer.returnValue(reader.current_piece_data)
        
        if piece not in self.waiting_pieces:
            self.waiting_pieces[piece] = defer.Deferred()
        
        logger.debug('Waiting for %s' % piece)
        self.torrent.schedule_piece(self, piece, 0)
        while not self.torrent.torrent.handle.have_piece(piece):
            if self.do_shutdown:
                raise Exception()
            logger.debug('Did not have piece %i, waiting' % piece)
            self.torrent.unrelease()
            yield sleep(1)
        
        self.torrent.torrent.handle.read_piece(piece)
        
        data = yield self.waiting_pieces[piece]
        if piece in self.waiting_pieces:
            del self.waiting_pieces[piece]
        logger.debug('Done waiting for piece %i, returning data' % piece)
        defer.returnValue(data)
    
    def shutdown(self):
        #self.alerts.deregister_handler(self.on_alert_torrent_finished)
        self.do_shutdown = True

class Torrent(object):
    def __init__(self, torrent_handler, infohash):
        self.infohash = infohash
        self.torrent = component.get("TorrentManager").torrents.get(infohash, None)
        self.torrent_handler = torrent_handler
        
        if not self.torrent:
            raise UnknownTorrentException('%s is not a known infohash' % infohash)
        
        self.torrent_files = None
        self.priority_increased = defaultdict(set)
        self.do_shutdown = False
        self.torrent_released = False # set to True if all the files are set to download
        
        
        self.populate_files()
        self.file_priorities = [0] * len(self.torrent_files)
        
        self.last_piece = self.torrent_files[-1].last_piece
        self.torrent.handle.set_sequential_download(True)
        reactor.callLater(0, self.update_piece_priority)
        reactor.callLater(0, self.blackhole_all_pieces, 0, self.last_piece)
    
    def populate_files(self):
        self.torrent_files = []
        
        status = self.torrent.get_status(['piece_length', 'files', 'save_path'])
        piece_length = status['piece_length']
        files = status['files']
        save_path = status['save_path']
        
        for f in files:
            first_piece = f['offset'] / piece_length
            last_piece = (f['offset'] + f['size']) / piece_length
            full_path = os.path.join(save_path, f['path'])
            
            path = f['path']
            if '/' in path:
                path = '/'.join(path.split('/')[1:])
            
            self.torrent_files.append(TorrentFile(self, first_piece, last_piece, piece_length, f['offset'],
                                                  path, full_path, f['size'], f['index']))
        
        return files
    
    def find_file(self, file_or_index=None):
        best_file = None
        biggest_file_size = 0
        
        for i, f in enumerate(self.torrent_files):
            logger.debug('Testing file %r against %s / %r' % (file_or_index, i, f.path))
            if file_or_index is not None:
                if i == file_or_index or f.path == file_or_index:
                    best_file = f
                    break
            else:
                if f.size > biggest_file_size:
                    best_file = f
                    biggest_file_size = f.size
        
        return best_file
    
    def get_file(self, file_or_index=None):
        f = self.find_file(file_or_index)
        if f is None:
            raise UnknownFileException('Was unable to find %s' % file_or_index)
        
        return f
    
    def unprioritize_pieces(self, tfr):
        logger.debug('Unprioritizing pieces for %s' % tfr)
        currently_downloading = self.get_currently_downloading()
        
        for piece, increased_by in self.priority_increased.items():
            if tfr in increased_by:
                increased_by.remove(tfr)
                if not increased_by and piece not in currently_downloading and not self.torrent.status.pieces[piece]:
                    logger.debug('Unprioritizing piece %s' % piece)
                    self.torrent.handle.piece_priority(piece, 0)
    
    def get_currently_downloading(self):
        currently_downloading = set()
        for peer in self.torrent.handle.get_peer_info():
            if peer.downloading_piece_index != -1:
                currently_downloading.add(peer.downloading_piece_index)
        
        return currently_downloading
    
    def blackhole_all_pieces(self, first_piece, last_piece):
        currently_downloading = self.get_currently_downloading()
        
        logger.debug('Blacklisting pieces from %i to %i skipping %r' % (first_piece, last_piece, currently_downloading))
        for piece in range(first_piece, last_piece+1):
            if piece not in currently_downloading and not self.torrent.status.pieces[piece]:
                if piece in self.priority_increased:
                    continue
                logger.debug('Setting piece priority %s to blacklist' % piece)
                self.torrent.handle.piece_priority(piece, 0)
    
    def unrelease(self):
        if self.torrent_released:
            logger.debug('Unreleasing %s' % self.infohash)
            self.torrent_released = False
            self.torrent.set_file_priorities(self.file_priorities)
            self.blackhole_all_pieces(0, self.last_piece)
    
    def get_torrent_file(self, file_or_index):
        f = self.get_file(file_or_index)
        f.file_requested = True
        
        self.torrent.resume()
        
        should_update_priorities = False
        if self.file_priorities[f.index] == 0:
            self.file_priorities[f.index] = 3
            should_update_priorities = True
        
        if self.torrent_released:
            should_update_priorities = True
        
        if should_update_priorities and not f.is_complete(): # Need to do this stuff on seek too
            self.unrelease()
        
        return f
    
    def shutdown(self):
        logger.info('Shutting down torrent %s' % self.infohash)
        self.do_shutdown = True
        
        for tf in self.torrent_files:
            tf.shutdown()
    
    def schedule_piece(self, torrent_file, piece, distance):
        if torrent_file not in self.priority_increased[piece]:
            if not self.priority_increased[piece]:
                self.priority_increased[piece].add(torrent_file)
        
                logger.debug('Scheduled piece %s at distance %s' % (piece, distance))
                
                self.torrent.handle.piece_priority(piece, (7 if distance <= 4 else 6))
                self.torrent.handle.set_piece_deadline(piece, 700*(distance+1))
            
            self.priority_increased[piece].add(torrent_file)
    
    def do_pieces_schedule(self, torrent_file, currently_downloading, from_piece):
        logger.debug('Looking for stuff to do with pieces for file %s from piece %s' % (torrent_file, from_piece))
        
        priority_increased = 0
        chain_size = 0
        download_chain_size = 0
        end_of_chain = False
        
        current_buffer_offset = 5
        if self.torrent.status.pieces[torrent_file.last_piece]:
            if torrent_file.first_piece != torrent_file.last_piece and self.torrent.status.pieces[torrent_file.last_piece-1] \
                    or torrent_file.first_piece == torrent_file.last_piece:
                current_buffer_offset = 20
        
        for piece, status in enumerate(self.torrent.status.pieces[from_piece:torrent_file.last_piece+1], from_piece):
            if not end_of_chain:
                if status:
                    chain_size += 1
                elif piece in currently_downloading:
                    download_chain_size += 1
            
            if not status and piece not in currently_downloading:
                if not end_of_chain:
                    status_increase = max(11, chain_size-current_buffer_offset)
                end_of_chain = True
                
                priority_increased += 1
                if priority_increased >= status_increase:
                    logger.debug('Done increasing priority for %i pieces' % status_increase)
                    break
                
                self.schedule_piece(torrent_file, piece, piece-from_piece)
        else:
            logger.info('We are done with the rest of this chain, we might be able to increase others')
            return True
        
        return False
    
    def update_piece_priority(self): # if all do_pieces_schedule returns true, allow all pices of file to be downloaded or whole torernt
        if self.do_shutdown:
            return
        
        logger.debug('Updating piece priority for %s' % self.infohash)
        
        currently_downloading = set()
        for peer in self.torrent.handle.get_peer_info():
            if peer.downloading_piece_index != -1:
                currently_downloading.add(peer.downloading_piece_index)
        
        all_heads_done = True
        for f in self.torrent_files:
            if not f.file_requested and not f.current_readers:
                continue
            
            logger.debug('Rescheduling file %s' % f.path)
            
            if f.file_requested:
                all_heads_done &= self.do_pieces_schedule(f, currently_downloading, f.first_piece)
                self.schedule_piece(f, f.last_piece, 0)
                if f.first_piece != f.last_piece:
                    self.schedule_piece(f, f.last_piece-1, 1)
            
            for tfr in f.current_readers:
                if tfr.waiting_for_piece is not None:
                    logger.debug('Scheduling based on waiting for piece %s' % tfr.waiting_for_piece)
                    all_heads_done &= self.do_pieces_schedule(f, currently_downloading, tfr.waiting_for_piece)
                elif tfr.current_piece is not None:
                    logger.debug('Scheduling based on current piece %s' % tfr.current_piece)
                    all_heads_done &= self.do_pieces_schedule(f, currently_downloading, tfr.current_piece)
        
        if all(self.torrent.status.pieces):
            logger.debug('All pieces complete, no need to loop')
            return
        
        if all_heads_done and not self.torrent_released:
            logger.debug('We are already done with all heads, figuring out what to do next')
            
            if self.torrent_handler.config['reset_complete']:
                self.torrent_released = True
                logger.debug('Resetting all disabled files')
                file_priorities = [(1 if fp == 0 else fp) for fp in self.file_priorities]
                self.torrent.set_file_priorities(file_priorities)
        
        if not all_heads_done and self.torrent_released:
            logger.debug('Seems like the torrent was released too early')
            self.unrelease()
        
        reactor.callLater(0.3, self.update_piece_priority)

class TorrentHandler(object):
    def __init__(self, config):
        self.torrents = {}
        self.config = config
        
        self.alerts = component.get("AlertManager")
        self.alerts.register_handler("torrent_removed_alert", self.on_alert_torrent_removed)
    
    def get_stream(self, infohash, file_or_index=None):
        logger.info('Trying to stream infohash %s and file %s' % (infohash, file_or_index))
        if infohash not in self.torrents:
            self.torrents[infohash] = Torrent(self, infohash)
        
        return self.torrents[infohash].get_torrent_file(file_or_index)
    
    def on_alert_torrent_removed(self, alert):
        try:
            torrent_id = str(alert.handle.info_hash())
        except (RuntimeError, KeyError):
            logger.warning('Failed to handle on torrent remove alert')
            return
        
        if torrent_id not in self.torrents:
            return
        
        self.torrents[torrent_id].shutdown()
        del self.torrents[torrent_id]
    
    def shutdown(self):
        logger.debug('Shutting down TorrentHandler')
        self.alerts.deregister_handler(self.on_alert_torrent_removed)
        for torrent in self.torrents.values():
            torrent.shutdown()

class Core(CorePluginBase):
    def enable(self):
        self.config = deluge.configmanager.ConfigManager("streaming.conf", DEFAULT_PREFS)
        self.fsr = FileServeResource()
        
        self.resource = Resource()
        self.resource.putChild('file', self.fsr)
        if self.config['allow_remote']:
            self.resource.putChild('stream', StreamResource(username=self.config['remote_username'],
                                                            password=self.config['remote_password'],
                                                            client=self))
        
        self.site = server.Site(self.resource)
        
        try:
            session = component.get("Core").session
            settings = session.get_settings()
            settings['prioritize_partial_pieces'] = True
            session.set_settings(settings)
        except AttributeError:
            logger.warning('Unable to exclude partial pieces')
        
        self.torrent_handler = TorrentHandler(self.config)
        
        try:
            self.listening = reactor.listenTCP(self.config['port'], self.site, interface=self.config['ip'])
        except:
            self.listening = reactor.listenTCP(self.config['port'], self.site, interface='127.0.0.1')

    @defer.inlineCallbacks
    def disable(self):
        self.site.stopFactory()
        self.torrent_handler.shutdown()
        yield self.listening.stopListening()

    def update(self):
        pass

    @export
    @defer.inlineCallbacks
    def set_config(self, config):
        """Sets the config dictionary"""
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
    def stream_torrent(self, infohash=None, url=None, filedump=None, filepath_or_index=None):
        tor = component.get("TorrentManager").torrents.get(infohash, None)
        
        if tor is None:
            logger.info('Did not find torrent, must add it')
            
            if not filedump and url:
                filedump = yield client.getPage(url)
            
            if not filedump:
                defer.returnValue({'status': 'error', 'message': 'unable to find torrent, provide infohash, url or filedump'})
            
            torrent_info = lt.torrent_info(lt.bdecode(filedump))
            infohash = str(torrent_info.info_hash())
        
            core = component.get("Core")
            try:
                yield core.add_torrent_file('file.torrent', filedump.encode('base64'), {'add_paused': True})
            except:
                defer.returnValue({'status': 'error', 'message': 'failed to add torrent'})
            
        try:
            tf = self.torrent_handler.get_stream(infohash, filepath_or_index)
        except UnknownTorrentException:
            defer.returnValue({'status': 'error', 'message': 'unable to find torrent, probably failed to add it'})
        
        defer.returnValue({
            'status': 'success',
            'use_stream_urls': self.config['use_stream_urls'],
            'auto_open_stream_urls': self.config['auto_open_stream_urls'],
            'url': 'http://%s:%s/file/%s/%s' % (self.config.config['ip'], self.config.config['port'],
                                                self.fsr.add_file(tf), urllib.quote_plus(os.path.basename(tf.path)))
        })