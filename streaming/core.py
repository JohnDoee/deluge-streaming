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
from copy import copy

from deluge import component, configmanager
from deluge._libtorrent import lt
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase

from twisted.internet import reactor, defer
from twisted.python import randbytes
from twisted.web import server, resource, static, client

from .filelike import FilelikeObjectResource
from .resource import Resource

logger = logging.getLogger(__name__)

DEFAULT_PREFS = {
    'ip': '127.0.0.1',
    'port': 46123,
    'allow_remote': False,
    'download_only_streamed': False,
    'use_stream_urls': False,
    'auto_open_stream_urls': False,
    'use_ssl': False,
    'remote_username': 'username',
    'remote_password': 'password',
    'serve_method': 'standalone',
    'ssl_source': 'daemon',
    'ssl_priv_key_path': '',
    'ssl_cert_path': '',
}

PRIORITY_INCREASE = 5


def sleep(seconds):
    d = defer.Deferred()
    reactor.callLater(seconds, d.callback, seconds)
    return d


class ServerContextFactory(object):
    def __init__(self, cert_file, key_file):
        self._cert_file = cert_file
        self._key_file = key_file

    def getContext(self):
        from OpenSSL import SSL

        method = getattr(SSL, 'TLSv1_1_METHOD', None)
        if method is None:
            method = getattr(SSL, 'SSLv23_METHOD', None)

        ctx = SSL.Context(method)
        ctx.use_certificate_file(self._cert_file)
        ctx.use_certificate_chain_file(self._cert_file)
        ctx.use_privatekey_file(self._key_file)
        return ctx


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
        key = request.postpath[0]
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
    def render_POST(self, request):
        infohash = request.args.get('infohash')
        path = request.args.get('path')
        wait_for_end_pieces = bool(request.args.get('wait_for_end_pieces'))

        if path:
            path = path[0]
        else:
            path = None

        if infohash:
            infohash = infohash[0]
        else:
            infohash = infohash

        payload = request.content.read()
        if not payload:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'invalid torrent'}))

        result = yield self.client.stream_torrent(infohash=infohash, filedump=payload, filepath_or_index=path, wait_for_end_pieces=wait_for_end_pieces)
        defer.returnValue(json.dumps(result))

    @defer.inlineCallbacks
    def render_GET(self, request):
        infohash = request.args.get('infohash')
        path = request.args.get('path')
        wait_for_end_pieces = bool(request.args.get('wait_for_end_pieces'))

        if not infohash:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'missing infohash'}))

        infohash = infohash[0]

        if path:
            path = path[0]
        else:
            path = None

        result = yield self.client.stream_torrent(infohash=infohash, filepath_or_index=path, wait_for_end_pieces=wait_for_end_pieces)
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

        logger.debug('We can read from local piece from %s size %s from position %s - size of current payload %s' % (read_position, size, self.position, len(self.current_piece_data)))
        data = self.current_piece_data[read_position:read_position+size]
        self.position += len(data)

        defer.returnValue(data)

    def tell(self):
        return self.position

    def close(self):
        self.torrent_file.close(self)

    def seek(self, offset, whence=os.SEEK_SET):
        self.position = offset


class TorrentFile(object):  # can be read from, knows about itself
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
        self.file_requested_once = False
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

    def is_complete(self):
        torrent_status = self.torrent.torrent.get_status(['file_progress', 'state'])
        file_progress = torrent_status['file_progress']
        return file_progress and file_progress[self.index] == 1.0

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

        if alert.buffer is None:
            return

        piece_data = copy(alert.buffer)
        cbs = self.waiting_pieces.pop(alert.piece, [])

        for cb in cbs:
            cb.callback(piece_data)

    @defer.inlineCallbacks
    def wait_for_end_pieces(self):
        handle = self.torrent.torrent.handle
        for piece in [self.first_piece, self.last_piece]:
            handle.set_piece_deadline(piece, 0)
            handle.piece_priority(piece, 7)

        while not handle.have_piece(self.first_piece) and not handle.have_piece(self.last_piece):
            if self.do_shutdown:
                raise Exception('Shutting down')
            logger.debug('Did not have piece %i, waiting' % piece)
            yield sleep(1)

    @defer.inlineCallbacks
    def get_piece_data(self, piece):
        logger.debug('Trying to get piece data for piece %s' % piece)
        for reader in self.current_readers:
            if reader.current_piece == piece:
                defer.returnValue(reader.current_piece_data)

        if piece not in self.waiting_pieces:
            created_waiting_defer = True
            self.waiting_pieces[piece] = []
        else:
            created_waiting_defer = False

        d = defer.Deferred()
        self.waiting_pieces[piece].append(d)

        logger.debug('Waiting for %s' % piece)
        while not self.torrent.torrent.handle.have_piece(piece):
            if self.do_shutdown:
                raise Exception('Shutting down')
            logger.debug('Did not have piece %i, waiting' % piece)
            yield sleep(1)

        if created_waiting_defer:
            self.torrent.torrent.handle.read_piece(piece)

        data = yield d
        logger.debug('Done waiting for piece %i, returning data' % piece)
        defer.returnValue(data)

    def shutdown(self):
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
        self.torrent_released = True  # set to True if all the files are set to download

        self.populate_files()
        self.file_priorities = [0] * len(self.torrent_files)

        self.last_piece = self.torrent_files[-1].last_piece
        self.torrent.handle.set_sequential_download(True)
        self.torrent.handle.set_priority(1)
        reactor.callLater(0, self.update_piece_priority)

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

            self.torrent_files.append(TorrentFile(self, first_piece, last_piece, piece_length, f['offset'],
                                                  f['path'], full_path, f['size'], f['index']))

        return files

    def find_file(self, file_or_index=None, includes_name=False):
        best_file = None
        biggest_file_size = 0

        for i, f in enumerate(self.torrent_files):
            path = f.path
            if not includes_name and '/' in path:
                path = '/'.join(path.split('/')[1:])

            logger.debug('Testing file %r against %s / %r' % (file_or_index, i, path))
            if file_or_index is not None:
                if i == file_or_index or path == file_or_index:
                    best_file = f
                    break
            else:
                if f.size > biggest_file_size:
                    best_file = f
                    biggest_file_size = f.size

        return best_file

    def get_file(self, file_or_index=None, includes_name=False):
        f = self.find_file(file_or_index, includes_name)
        if f is None:
            raise UnknownFileException('Was unable to find %s' % file_or_index)

        return f

    def get_currently_downloading(self):
        currently_downloading = set()
        for peer in self.torrent.handle.get_peer_info():
            if peer.downloading_piece_index != -1:
                currently_downloading.add(peer.downloading_piece_index)

        return currently_downloading

    def get_torrent_file(self, file_or_index, includes_name):
        f = self.get_file(file_or_index, includes_name)
        f.file_requested = True
        f.file_requested_once = True

        self.torrent.resume()

        should_update_priorities = False
        if self.file_priorities[f.index] == 0:
            self.file_priorities[f.index] = 3
            should_update_priorities = True

        if self.torrent_released:
            should_update_priorities = True

        if should_update_priorities and not f.is_complete():  # Need to do this stuff on seek too
            self.torrent.set_file_priorities(self.file_priorities)

        return f

    def shutdown(self):
        logger.info('Shutting down torrent %s' % (self.infohash, ))

        self.torrent.handle.set_priority(0)

        for piece, status in enumerate(self.torrent.status.pieces[0:self.last_piece+1]):
            if status:
                continue

            priority = self.torrent.handle.piece_priority(piece)
            if priority == 0:
                self.torrent.handle.piece_priority(piece, 1)

        if not self.torrent_handler.config['download_only_streamed']:
            logger.debug('Resetting file priorities')
            file_priorities = [(1 if fp == 0 else fp) for fp in self.file_priorities]
            self.torrent.set_file_priorities(file_priorities)

        self.do_shutdown = True
        self.torrent_handler.remove_torrent(self.infohash)

        for tf in self.torrent_files:
            tf.shutdown()

    def update_piece_priority(self):  # if file streamed has reached end, unblacklist all prior pieces
        if self.do_shutdown:
            return

        logger.debug('Updating piece priority for %s' % (self.infohash, ))
        currently_downloading = self.get_currently_downloading()

        for f in self.torrent_files:
            if not f.file_requested and not f.current_readers:  # nobody wants the file and nobody is watching
                continue

            logger.debug('Rescheduling file %s' % (f.path, ))

            heads = set()
            if f.file_requested:  # we expect a piece head to be at start
                heads.add(f.first_piece)

            waiting_for_pieces = set()

            for tfr in f.current_readers:
                if tfr.waiting_for_piece is not None:
                    waiting_for_pieces.add(tfr.waiting_for_piece)

                piece = max(tfr.waiting_for_piece, tfr.current_piece)
                if piece is not None:
                    heads.add(piece)

            if not heads:
                continue

            first_head = min(heads)

            for head_piece in heads:
                priority_increased = 0
                for piece, status in enumerate(self.torrent.status.pieces[head_piece:f.last_piece+1], head_piece):
                    if status or piece in currently_downloading:
                        continue

                    priority = self.torrent.handle.piece_priority(piece)
                    if priority_increased < PRIORITY_INCREASE:
                        priority_increased += 1

                        if piece in waiting_for_pieces:
                            if priority < 7:
                                logger.debug('setting priority for %s to 7 with deadline 0' % (piece, ))

                                self.torrent.handle.set_piece_deadline(piece, 0)
                                self.torrent.handle.piece_priority(piece, 7)
                        elif priority < 6:
                            deadline = 3000 * priority_increased
                            logger.debug('setting priority for %s to 6 with deadline %s' % (piece, deadline, ))
                            self.torrent.handle.piece_priority(piece, 6)
                            self.torrent.handle.set_piece_deadline(piece, deadline)

                    elif priority == 0:
                        self.torrent.handle.piece_priority(piece, 1)

                if head_piece == first_head:
                    if priority_increased < PRIORITY_INCREASE:
                        logger.debug('Everything we need has been scheduled, looking for pieces across file to unblacklist')
                        for piece, status in enumerate(self.torrent.status.pieces[f.first_piece:f.last_piece+1], f.first_piece):
                            if status:
                                continue

                            priority = self.torrent.handle.piece_priority(piece)
                            if priority == 0:
                                self.torrent.handle.piece_priority(piece, 1)
                    else:
                        logger.debug('Looking for pieces before smallest head %s to blacklist' % (first_head, ))
                        for piece, status in enumerate(self.torrent.status.pieces[f.first_piece:first_head], f.first_piece):
                            if status or piece in currently_downloading:
                                continue

                            if self.torrent.handle.piece_priority(piece) != 0:
                                logger.debug('Blacklisting %i' % (piece, ))
                                self.torrent.handle.piece_priority(piece, 0)

        found_requested = False
        for f in self.torrent_files:
            if f.file_requested_once:
                found_requested = True
                if not f.is_complete() or f.current_readers:
                    break
        else:
            if found_requested:
                logger.debug('Nobody is currently using %s, shutting down torrent-handler' % (self.infohash, ))
                self.shutdown()

        reactor.callLater(1, self.update_piece_priority)


class TorrentHandler(object):
    def __init__(self, config):
        self.torrents = {}
        self.config = config

        self.alerts = component.get("AlertManager")
        self.alerts.register_handler("torrent_removed_alert", self.on_alert_torrent_removed)

    def get_stream(self, infohash, file_or_index=None, includes_name=False):
        logger.info('Trying to stream infohash %s and file %s include_name %s' % (infohash, file_or_index, includes_name))
        if infohash not in self.torrents:
            self.torrents[infohash] = Torrent(self, infohash)

        return self.torrents[infohash].get_torrent_file(file_or_index, includes_name)

    def on_alert_torrent_removed(self, alert):
        try:
            torrent_id = str(alert.handle.info_hash())
        except (RuntimeError, KeyError):
            logger.warning('Failed to handle on torrent remove alert')
            return

        if torrent_id not in self.torrents:
            return

        self.torrents[torrent_id].shutdown()
        self.remove_torrent(torrent_id)

    def remove_torrent(self, torrent_id):
        del self.torrents[torrent_id]

    def shutdown(self):
        logger.debug('Shutting down TorrentHandler')
        self.alerts.deregister_handler(self.on_alert_torrent_removed)
        for torrent in self.torrents.values():
            torrent.shutdown()


class Core(CorePluginBase):
    listening = None
    base_url = None

    def enable(self):
        self.config = deluge.configmanager.ConfigManager("streaming.conf", DEFAULT_PREFS)

        try:
            session = component.get("Core").session
            settings = session.get_settings()
            settings['prioritize_partial_pieces'] = True
            session.set_settings(settings)
        except AttributeError:
            logger.warning('Unable to exclude partial pieces')

        self.fsr = FileServeResource()
        resource = Resource()
        resource.putChild('file', self.fsr)
        if self.config['allow_remote']:
            resource.putChild('stream', StreamResource(username=self.config['remote_username'],
                                                       password=self.config['remote_password'],
                                                       client=self))

        base_resource = Resource()
        base_resource.putChild('streaming', resource)
        self.site = server.Site(base_resource)

        self.torrent_handler = TorrentHandler(self.config)

        plugin_manager = component.get("CorePluginManager")
        logger.warning('plugins %s' % (plugin_manager.get_enabled_plugins(), ))

        self.base_url = 'http'
        if self.config['serve_method'] == 'standalone':
            if self.config['use_ssl'] and self.check_ssl():  # use default deluge (or webui), input custom
                if self.config['ssl_source'] == 'daemon':
                    web_config = configmanager.ConfigManager("web.conf", {"pkey": "ssl/daemon.pkey",
                                                                          "cert": "ssl/daemon.cert"})

                    context = ServerContextFactory(configmanager.get_config_dir(web_config['cert']),
                                                   configmanager.get_config_dir(web_config['pkey']))
                elif self.config['ssl_source'] == 'custom':
                    context = ServerContextFactory(self.config['ssl_cert_path'],
                                                   self.config['ssl_priv_key_path'])

                try:
                    self.listening = reactor.listenSSL(self.config['port'], self.site, context, interface=self.config['ip'])
                except:
                    self.listening = reactor.listenSSL(self.config['port'], self.site, context, interface='0.0.0.0')
                self.base_url += 's'
            else:
                try:
                    self.listening = reactor.listenTCP(self.config['port'], self.site, interface=self.config['ip'])
                except:
                    self.listening = reactor.listenTCP(self.config['port'], self.site, interface='0.0.0.0')

            port = self.config['port']
            ip = self.config['ip']
        elif self.config['serve_method'] == 'webui' and self.check_webui():  # this webserver is fubar
            plugin_manager = component.get("CorePluginManager")

            webui_plugin = plugin_manager['WebUi'].plugin
            webui_plugin.server.top_level.putChild('streaming', resource)

            port = webui_plugin.server.port
            ip = getattr(webui_plugin.server, 'interface', None) or self.config['ip']
            if webui_plugin.server.https:
                self.base_url += 's'
        else:
            raise NotImplementedError()

        self.base_url += '://'
        if ':' in ip:
            self.base_url += ip
        else:
            self.base_url += '%s:%s' % (ip, port)

    @defer.inlineCallbacks
    def disable(self):
        self.site.stopFactory()
        self.torrent_handler.shutdown()

        if self.check_webui():
            plugin_manager = component.get("CorePluginManager")
            webui_plugin = plugin_manager['WebUi'].plugin

            try:
                webui_plugin.server.top_level.delEntity('streaming')
            except KeyError:
                pass

        if self.listening:
            yield self.listening.stopListening()
        self.listening = None

    def update(self):
        pass

    def check_ssl(self):
        if self.config['ssl_source'] == 'daemon':
            return True

        if not os.path.isfile(self.config['ssl_priv_key_path']) or not os.access(self.config['ssl_priv_key_path'], os.R_OK):
            return False

        if not os.path.isfile(self.config['ssl_cert_path']) or not os.access(self.config['ssl_cert_path'], os.R_OK):
            return False

        return True

    def check_webui(self):
        plugin_manager = component.get("CorePluginManager")
        return 'WebUi' in plugin_manager.get_enabled_plugins()

    def check_config(self):
        pass

    @export
    @defer.inlineCallbacks
    def set_config(self, config):
        self.previous_config = copy(self.config)

        for key in config.keys():
            self.config[key] = config[key]
        self.config.save()

        yield self.disable()
        self.enable()

        if self.config['serve_method'] == 'standalone' and self.config['ssl_source'] == 'custom' and self.config['use_ssl']:
            if not self.check_ssl():
                defer.returnValue(('error', 'ssl', 'SSL not enabled, make sure the private key and certificate exist and are accessible'))

    @export
    def get_config(self):
        """Returns the config dictionary"""
        return self.config.config

    @export
    @defer.inlineCallbacks
    def stream_torrent(self, infohash=None, url=None, filedump=None, filepath_or_index=None, includes_name=False, wait_for_end_pieces=False):
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
            tf = self.torrent_handler.get_stream(infohash, filepath_or_index, includes_name)
        except UnknownTorrentException:
            defer.returnValue({'status': 'error', 'message': 'unable to find torrent, probably failed to add it'})

        if wait_for_end_pieces:
            logger.debug('Waiting for end pieces')
            yield tf.wait_for_end_pieces()

        filename = os.path.basename(tf.path).encode('utf-8')
        defer.returnValue({
            'status': 'success',
            'filename': filename,
            'use_stream_urls': self.config['use_stream_urls'],
            'auto_open_stream_urls': self.config['auto_open_stream_urls'],
            'url': '%s/streaming/file/%s/%s' % (self.base_url, self.fsr.add_file(tf),
                                                urllib.quote_plus(filename))
        })
