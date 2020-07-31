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
import random
import string
import time

import deluge.configmanager

from copy import copy
from datetime import datetime, timedelta
from types import MethodType

from deluge import component, configmanager
from deluge._libtorrent import lt
from deluge.core.rpcserver import export
from deluge.plugins.pluginbase import CorePluginBase

from twisted.internet import reactor, defer, task, error
from twisted.web import server, client
from twisted.web.resource import Resource as TwistedResource

from thomas import router, Item, OutputBase

from .resource import Resource
from .torrentfile import DelugeTorrentInput

router.register_handler(DelugeTorrentInput.plugin_name, DelugeTorrentInput, True, False, False)

VIDEO_STREAMABLE_EXTENSIONS = ['mkv', 'mp4', 'iso', 'ogg', 'ogm', 'm4v']
AUDIO_STREAMABLE_EXTENSIONS = ['flac', 'mp3', 'oga']
STREAMABLE_EXTENSIONS = set(VIDEO_STREAMABLE_EXTENSIONS + AUDIO_STREAMABLE_EXTENSIONS)
TORRENT_CLEANUP_INTERVAL = timedelta(minutes=30)
MAX_FILE_PRIORITY = 2
MAX_PIECE_PRIORITY = 7
MIN_WAIT_PIECE_PRIORITY_DELAY = timedelta(seconds=5)
WITHIN_CHAIN_PERCENTAGE = 0.10
MIN_PIECE_COUNT_FOR_CHAIN_CONSIDERATION = 40
MIN_CHAIN_WAIT_DELAY = timedelta(seconds=8)


DEFAULT_PREFS = {
    'ip': '127.0.0.1',
    'port': 46123,
    'allow_remote': False,
    'download_only_streamed': False,
    'use_stream_urls': False,
    'auto_open_stream_urls': False,
    'use_ssl': False,
    'remote_username': 'stream',
    'remote_password': ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(16)),
    'reverse_proxy_enabled': False,
    'reverse_proxy_base_url': '',
    'serve_method': 'standalone',
    'ssl_source': 'daemon',
    'ssl_priv_key_path': '',
    'ssl_cert_path': '',
    'aggressive_prioritizing': False,
}

logger = logging.getLogger(__name__)


def sleep(secs):
    d = defer.Deferred()
    reactor.callLater(secs, d.callback, None)
    return d


def get_torrent(infohash):
    # Taken from newer Deluge source to allow for backward compatibility.
    def get_file_priorities(self):
        """Return the file priorities"""
        if not self.handle.has_metadata():
            return []

        if not self.options["file_priorities"]:
            # Ensure file_priorities option is populated.
            self.set_file_priorities([])

        return list(self.options["file_priorities"])

    torrent = component.get("TorrentManager").torrents.get(infohash, None)
    if torrent and not hasattr(torrent, 'get_file_priorities'):
        torrent.get_file_priorities = MethodType(get_file_priorities, torrent)

    return torrent


class Torrent(object):
    def __init__(self, torrent_handler, infohash, aggressive_prioritizing=False):
        self.torrent_handler = torrent_handler
        self.infohash = infohash
        self.aggressive_prioritizing = aggressive_prioritizing

        self.filesets = {}
        self.readers = {}
        self.cycle_lock = defer.DeferredLock()
        self.last_activity = datetime.now()
        self.waited_pieces = set()

        self.torrent = get_torrent(infohash)
        status = self.torrent.get_status(['piece_length'])
        self.piece_length = status['piece_length']
        self.torrent.handle.set_sequential_download(True)
        self.torrent.handle.set_priority(1)

    def ensure_started(self):
        if self.torrent.status.paused:
            self.torrent.resume()

    def get_file_from_offset(self, offset):
        status = self.torrent.get_status(['files'])
        last_file = None
        for f in status['files']:
            if f['offset'] > offset:
                break

            last_file = f
        return last_file

    def can_read(self, from_byte):
        self.ensure_started()

        status = self.torrent.get_status(['pieces'])
        needed_piece, rest = divmod(from_byte, self.piece_length)
        last_available_piece = None
        for piece, status in enumerate(self.torrent.status.pieces[needed_piece:], needed_piece):
            if not status:
                break
            last_available_piece = piece

        if last_available_piece is None:
            logger.debug('Since we are waiting for a piece, we need to check if we should set piece %s to max' % (needed_piece, ))

            is_next_in_chain = False
            f = self.get_file_from_offset(from_byte)
            file_piece_count = (f['size'] // self.piece_length) + 1

            if file_piece_count <= MIN_PIECE_COUNT_FOR_CHAIN_CONSIDERATION:
                is_next_in_chain = True
            elif self.readers:
                best_reader_from_byte = max(reader[1] for reader in self.readers.values() if reader[1] <= from_byte)
                best_reader_piece = best_reader_from_byte // self.piece_length
                downloading_pieces = self.get_currently_downloading()
                # TODO: unfinished_piece can be None
                for unfinished_piece, status in enumerate(self.torrent.status.pieces[best_reader_piece:], best_reader_piece):
                    if not status and unfinished_piece not in downloading_pieces:
                        break

                piece_diff = best_reader_piece - unfinished_piece - 1
                if unfinished_piece >= best_reader_piece or piece_diff / file_piece_count <= WITHIN_CHAIN_PERCENTAGE:
                    is_next_in_chain = True
            else:
                is_next_in_chain = True

            if not is_next_in_chain or self.aggressive_prioritizing:
                logger.debug('Not a next-in-chain piece or aggressive prioritization enabled, setting priority now')
                self.torrent.handle.set_piece_deadline(needed_piece, 0)
                self.torrent.handle.piece_priority(needed_piece, MAX_PIECE_PRIORITY)

            file_priorities = list(self.torrent.get_file_priorities())
            if file_priorities[f['index']] != MAX_FILE_PRIORITY:
                logger.debug('Also setting file to max %r' % (f, ))
                file_priorities[f['index']] = MAX_FILE_PRIORITY
                self.torrent.set_file_priorities(file_priorities)

            for i in range(300):
                if self.torrent.status.pieces[needed_piece]:
                    break

                if not reactor.running:
                    return

                if is_next_in_chain and i == MIN_CHAIN_WAIT_DELAY.total_seconds() * 5 and needed_piece not in self.get_currently_downloading():
                    logger.debug('Next in chain waiting failed, setting priority')
                    self.torrent.handle.set_piece_deadline(needed_piece, 0)
                    self.torrent.handle.piece_priority(needed_piece, MAX_PIECE_PRIORITY)

                time.sleep(0.2)
                status = self.torrent.get_status(['pieces'])

            logger.debug('Calling read again to get the real number')
            return self.can_read(from_byte)
        else:
            logger.debug('Really last available piece is %s' % (last_available_piece, ))
            return ((last_available_piece - needed_piece) * self.piece_length) + self.piece_length - rest, last_available_piece

    def is_idle(self):
        return not self.readers and self.last_activity + TORRENT_CLEANUP_INTERVAL < datetime.now()

    def add_reader(self, filelike, path, from_byte, to_byte):
        logger.debug('Added reader %s path:%s from_byte:%s' % (filelike, path, from_byte, ))
        self.readers[filelike] = (path, from_byte, to_byte)

        self.cycle()

    def remove_reader(self, filelike):
        if filelike in self.readers:
            logger.debug('Removed reader %s' % (filelike, ))
            del self.readers[filelike]
            self.cycle()
            self.last_activity = datetime.now()

    def cycle(self):
        @defer.inlineCallbacks
        def handle_cycle():
            yield self.cycle_lock.acquire()
            try:
                self._cycle()
            except:
                logger.exception('Failed to cycle')
            self.cycle_lock.release()
        reactor.callFromThread(handle_cycle)

    def _cycle(self):
        logger.debug('Doing a cycle')

        found_not_started = False
        cannot_blacklist = set()
        must_whitelist = set()
        first_files = set()
        for fileset in self.filesets.values():
            logger.debug('Fileset %r' % (fileset, ))
            if not fileset['started']:
                found_not_started = True
                must_whitelist |= set(fileset['files'])
                fileset['started'] = True
            cannot_blacklist |= set(fileset['files'])
            first_files.add(fileset['files'][0])

        if found_not_started:
            self.ensure_started()

            logger.debug('We had a fileset not started, must_whitelist:%r first_files:%r cannot_blacklist:%r' % (must_whitelist, first_files, cannot_blacklist))
            status = self.torrent.get_status(['files', 'file_progress'])

            file_priorities = list(self.torrent.get_file_priorities())
            for f, progress in zip(status['files'], status['file_progress']):
                i = f['index']
                if progress == 1.0:
                    file_priorities[i] = 1
                    continue

                if f['path'] in must_whitelist:
                    if f['path'] in first_files:
                        file_priorities[i] = MAX_FILE_PRIORITY
                    else:
                        file_priorities[i] = 1
                elif f['path'] not in cannot_blacklist:
                    file_priorities[i] = 0

            self.torrent.set_file_priorities(file_priorities)

        if self.readers:
            status = self.torrent.get_status(['files', 'file_progress', 'pieces'])
            file_ranges = {}
            fileset_ranges = {}
            for path, from_byte, to_byte in self.readers.values():
                logger.debug('Reader %s, %s, %s' % (path, from_byte, to_byte, ))
                if path in file_ranges:
                    file_ranges[path] = min(from_byte, file_ranges[path])
                else:
                    file_ranges[path] = from_byte

                reader_piece = from_byte // self.piece_length
                self.torrent.handle.set_piece_deadline(reader_piece, 0)
                self.torrent.handle.piece_priority(reader_piece, MAX_PIECE_PRIORITY)

                for fileset_hash, fileset in self.filesets.items():
                    if path in fileset['files']:
                        if fileset_hash in fileset_ranges:
                            fileset_ranges[fileset_hash] = min(fileset_ranges[fileset_hash], fileset['files'].index(path))
                        else:
                            fileset_ranges[fileset_hash] = fileset['files'].index(path)

            file_priorities = list(self.torrent.get_file_priorities())
            logger.debug('Fileset heads: %r' % (fileset_ranges, ))
            for fileset_hash, first_file in fileset_ranges.items():
                fileset = self.filesets[fileset_hash]
                logger.debug('From index %s' % (first_file, ))
                file_mapping = {f['path']: f['index'] for f in status['files']}
                for i, f in enumerate(fileset['files']):
                    index = file_mapping[f]
                    if i < first_file:
                        file_priorities[index] = 0
                    elif i == first_file:
                        file_priorities[index] = MAX_FILE_PRIORITY
                    else:
                        file_priorities[index] = 1

            self.torrent.set_file_priorities(file_priorities)

            currently_downloading = self.get_currently_downloading()
            logger.debug('File heads: %r' % (file_ranges, ))
            for f, progress in zip(status['files'], status['file_progress']):
                if progress == 1.0:
                    continue

                if f['path'] not in file_ranges:
                    continue

                first_piece = f['offset'] // self.piece_length
                current_piece = file_ranges[path] // self.piece_length
                last_piece = (f['offset'] + f['size']) // self.piece_length
                logger.debug('Configuring pieces first piece %s current piece %s - all before should be blacklisted' % (first_piece, current_piece))

                for piece, piece_status in enumerate(self.torrent.status.pieces[first_piece:last_piece], first_piece):
                    if piece_status or piece in currently_downloading:
                        continue

                    priority = self.torrent.handle.piece_priority(piece)
                    if piece == first_piece:
                        if priority == 0:
                            self.torrent.handle.piece_priority(piece, 1)
                        continue

                    if piece < current_piece:
                        self.torrent.handle.piece_priority(piece, 0)
                    else:
                        self.torrent.handle.piece_priority(piece, 1)

    def get_currently_downloading(self):
        currently_downloading = set()
        for peer in self.torrent.handle.get_peer_info():
            if peer.downloading_piece_index != -1:
                currently_downloading.add(peer.downloading_piece_index)

        return currently_downloading

    def reset_priorities(self):
        status = self.torrent.get_status(['pieces'])
        for piece in range(len(self.torrent.status.pieces)):
            self.torrent.handle.piece_priority(piece, 1)

        self.torrent.set_file_priorities([1] * len(self.torrent.get_file_priorities()))

    def shutdown(self):
        logger.debug('Shutting down torrent %r' % (self, ))
        for reader in self.readers.keys():
            reactor.callInThread(reader.close)

    def add_fileset(self, fileset):
        files = [f.path for f in fileset]
        fileset_hash = hash(','.join(files))

        if fileset_hash not in self.filesets:
            self.filesets[fileset_hash] = {'started': False, 'files': files}

    def request_piece(self, piece):
        self.torrent.handle.read_piece(piece)

    def new_piece_available(self, piece, data):
        logger.debug("New pice available: %s" % (piece, ))
        for reader in self.readers.keys():
            reader.new_piece_available(piece, data)


class TorrentHandler(object):
    def __init__(self, reset_priorities_on_finish, aggressive_prioritizing=False):
        self.torrents = {}
        self.reset_priorities_on_finish = reset_priorities_on_finish
        self.aggressive_prioritizing = aggressive_prioritizing

        self.alerts = component.get("AlertManager")
        self.alerts.register_handler("torrent_removed_alert", self.on_alert_torrent_removed)
        self.alerts.register_handler("torrent_finished_alert", self.on_alert_torrent_finished)
        self.alerts.register_handler("read_piece_alert", self.on_alert_read_piece)

        self.cleanup_looping_call = task.LoopingCall(self.cleanup)
        self.cleanup_looping_call.start(60)

    def on_alert_torrent_removed(self, alert):
        try:
            infohash = str(alert.handle.info_hash())
        except (RuntimeError, KeyError):
            logger.warning('Failed to handle on torrent remove alert')
            return

        if infohash not in self.torrents:
            return

        self.torrents[infohash].shutdown()
        del self.torrents[infohash]

    def on_alert_torrent_finished(self, alert):
        try:
            infohash = str(alert.handle.info_hash())
        except (RuntimeError, KeyError):
            logger.warning('Failed to handle on torrent finished alert')
            return

        if infohash not in self.torrents:
            return

        if self.reset_priorities_on_finish:
            self.torrents[infohash].reset_priorities()

    def on_alert_read_piece(self, alert):
        try:
            infohash = str(alert.handle.info_hash())
        except (RuntimeError, KeyError):
            logger.warning('Failed to handle on read piece alert')
            return

        if infohash not in self.torrents:
            return

        self.torrents[infohash].new_piece_available(alert.piece, alert.buffer)

    def shutdown(self):
        for torrent in self.torrents.values():
            if self.reset_priorities_on_finish:
                torrent.reset_priorities()
            torrent.shutdown()

        self.cleanup_looping_call.stop()

    def get_filesystem(self, infohash):
        torrent = get_torrent(infohash)
        status = torrent.get_status(['files', 'file_progress', 'save_path'])
        save_path = status['save_path']

        found_rar = False
        path_item_mapping = {}
        for f, progress in zip(status['files'], status['file_progress']):
            full_path = os.path.join(save_path, f['path'])
            if '/' in f['path']:
                path, fn = f['path'].rsplit('/', 1)
            else:
                fn = f['path']
                path = ''

            item = Item(fn, attributes={'size': f['size']})
            item.readable = True
            item.streamable = True
            path_item_mapping.setdefault(path, []).append(item)

            if progress == 1.0:
                item.add_route('file', True, False, False, kwargs={'path': full_path})
            else:
                item.add_route('torrent_file', True, False, False, kwargs={
                    'torrent_handler': self,
                    'infohash': infohash,
                    'offset': f['offset'],
                    'path': full_path,
                })
            item.add_route('direct', False, False, True)

            if not found_rar and fn.split('.')[-1].lower() == 'rar':
                found_rar = True

        path_mapping = {}
        for path, items in path_item_mapping.items():
            combined_path = []
            for path_part in (path + '/').split('/'):
                partial_path = '/'.join(combined_path)
                if partial_path not in path_mapping:
                    item = path_mapping[partial_path] = Item(partial_path.split('/')[-1])
                    item.streamable = True
                    item.add_route('direct', False, False, True, kwargs={'allowed_extensions': STREAMABLE_EXTENSIONS})
                    if found_rar:
                        item.add_route('rar', False, False, True, kwargs={'lazy': True})

                    if combined_path:
                        parent_path = '/'.join(combined_path[:-1])
                        path_mapping[parent_path].add_item(item)
                combined_path.append(path_part)

            for item in items:
                path_mapping[path].add_item(item)

        item = path_mapping[''].list()[0] # TODO: make not use an empty item
        item.parent_item = None
        return item

    def get_torrent(self, infohash):
        if infohash not in self.torrents:
            self.torrents[infohash] = Torrent(self, infohash, self.aggressive_prioritizing)
        return self.torrents[infohash]

    @defer.inlineCallbacks
    def stream(self, infohash, path, wait_for_end_pieces=False):
        logger.debug('Trying to get path:%s from infohash:%s' % (path, infohash))
        torrent = get_torrent(infohash)

        for _ in range(10):
            status = torrent.get_status(['piece_length'])
            if status['piece_length'] > 0:
                break
            yield sleep(0.2)

        local_torrent = self.get_torrent(infohash)

        filesystem = self.get_filesystem(infohash)
        if path:
            stream_item = filesystem.get_item_from_path(path)
            if stream_item and filesystem == stream_item and path != stream_item.id:
                stream_item = filesystem.get_item_from_path('%s/%s' % (filesystem.id, path))
        else:
            stream_item = filesystem

        logger.debug('Stream, path:%s infohash:%s stream_item:%r' % (path, infohash, stream_item))
        if stream_item is None:
            defer.returnValue(None)

        stream_result = stream_item.stream()
        logger.debug('Streamresult, path:%s infohash:%s stream_result:%r' % (path, infohash, stream_result))
        if stream_result is None:
            defer.returnValue(None)

        if hasattr(stream_result, 'get_read_items'):
            fileset = stream_result.get_read_items()
        else:
            fileset = [stream_result]
        self.torrents[infohash].add_fileset(fileset)

        if wait_for_end_pieces:
            local_torrent.ensure_started()
            logger.debug('We need to wait for pieces')
            first_file = fileset[0]
            last_file = fileset[-1]

            status = torrent.get_status(['piece_length', 'files', 'file_progress'])
            piece_length = status['piece_length']

            wait_for_pieces = []
            for f, progress in zip(status['files'], status['file_progress']):
                if progress == 1.0:
                    continue

                piece_count = f['size'] // piece_length

                if f['path'] == first_file.path:
                    piece, rest = divmod(f['offset'], piece_length)
                    rest = piece_length - rest
                    wait_for_pieces.append(piece)

                    if rest < 1024 and piece_count > 2:
                        wait_for_pieces.append(piece + 1)

                if f['path'] == last_file.path:
                    piece, rest = divmod(f['offset'] + f['size'], piece_length)
                    wait_for_pieces.append(piece)

                    if rest < 1024 and piece_count > 2:
                        wait_for_pieces.append(piece - 1)

            logger.debug('We want first and last piece first, these are the pieces: %r' % (wait_for_pieces, ))
            if wait_for_pieces:
                for piece in wait_for_pieces:
                    torrent.handle.set_piece_deadline(piece, 0)
                    torrent.handle.piece_priority(piece, MAX_PIECE_PRIORITY)

            for _ in range(220):
                status = torrent.get_status(['pieces'])
                for piece in wait_for_pieces:
                    if not torrent.status.pieces[piece]:
                        break
                else:
                    break

                yield sleep(0.2)

        defer.returnValue(stream_result)

    def cleanup(self):
        for infohash, torrent in self.torrents.items():
            if torrent.is_idle():
                logger.debug('Torrent %s is idle, killing it' % (torrent, ))
                torrent.shutdown()
                del self.torrents[infohash]


class ServerContextFactory(object):
    def __init__(self, cert_file, key_file):
        self._cert_file = cert_file
        self._key_file = key_file

    def getContext(self):
        from OpenSSL import SSL

        methods_names = ['TLSv1_2_METHOD', 'TLSv1_1_METHOD', 'SSLv23_METHOD']
        for method_name in methods_names:
            method = getattr(SSL, method_name, None)
            if method is not None:
                break

        ctx = SSL.Context(method)
        ctx.use_certificate_file(self._cert_file)
        ctx.use_certificate_chain_file(self._cert_file)
        ctx.use_privatekey_file(self._key_file)
        return ctx


class StreamResource(Resource):
    isLeaf = True

    def __init__(self, client, *args, **kwargs):
        self.client = client
        Resource.__init__(self, *args, **kwargs)

    @defer.inlineCallbacks
    def render_POST(self, request):
        infohash = request.args.get(b'infohash')
        path = request.args.get(b'path')
        wait_for_end_pieces = bool(request.args.get(b'wait_for_end_pieces'))
        label = request.args.get(b'label')

        if path:
            path = path[0].decode('utf-8')
        else:
            path = None

        if infohash:
            infohash = infohash[0].decode('utf-8')
        else:
            infohash = None

        if label:
            label = label[0].decode('utf-8')
        else:
            label = None

        payload = request.content.read()
        if not payload:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'invalid torrent'}).encode('utf-8'))

        result = yield self.client.stream_torrent(infohash=infohash, filedump=payload, filepath_or_index=path, wait_for_end_pieces=wait_for_end_pieces, label=label)
        defer.returnValue(json.dumps(result).encode('utf-8'))

    @defer.inlineCallbacks
    def render_GET(self, request):
        infohash = request.args.get(b'infohash')
        path = request.args.get(b'path')
        wait_for_end_pieces = bool(request.args.get(b'wait_for_end_pieces'))

        if not infohash:
            defer.returnValue(json.dumps({'status': 'error', 'message': 'missing infohash'}).encode('utf-8'))

        infohash = infohash[0].decode('utf-8')

        if path:
            path = path[0].decode('utf-8')
        else:
            path = None

        result = yield self.client.stream_torrent(infohash=infohash, filepath_or_index=path, wait_for_end_pieces=wait_for_end_pieces)
        defer.returnValue(json.dumps(result).encode('utf-8'))


class Core(CorePluginBase):
    listening = None
    base_url = None

    _is_enabled = False

    def enable(self):
        self._is_enabled = True
        self.config = deluge.configmanager.ConfigManager("streaming.conf", DEFAULT_PREFS)

        try:
            session = component.get("Core").session
            settings = session.get_settings()
            settings['prioritize_partial_pieces'] = True
            session.set_settings(settings)
        except AttributeError:
            logger.warning('Unable to prioritize partial pieces')

        http_output_cls = OutputBase.find_plugin('http')
        http_output = http_output_cls(url_prefix='file')
        http_output.start()

        self.thomas_http_output = http_output

        resource = TwistedResource()
        resource.putChild(b'file', http_output.resource)
        if self.config['allow_remote']:
            resource.putChild(b'stream', StreamResource(username=self.config['remote_username'],
                                                       password=self.config['remote_password'],
                                                       client=self))

        base_resource = TwistedResource()
        base_resource.putChild(b'streaming', resource)
        self.site = server.Site(base_resource)

        self.torrent_handler = TorrentHandler(self.config['download_only_streamed'] == False, self.config['aggressive_prioritizing'])

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
                    try:
                        self.listening = reactor.listenSSL(self.config['port'], self.site, context, interface='0.0.0.0')
                    except error.CannotListenError:
                        logger.warning("Unable to listen to anything")
                self.base_url += 's'
            else:
                try:
                    self.listening = reactor.listenTCP(self.config['port'], self.site, interface=self.config['ip'])
                except:
                    try:
                        self.listening = reactor.listenTCP(self.config['port'], self.site, interface='0.0.0.0')
                    except error.CannotListenError:
                        logger.warning("Unable to listen to anything")

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

        if self.config['reverse_proxy_enabled'] and self.config['reverse_proxy_base_url']:
            self.base_url = self.config['reverse_proxy_base_url']
        else:
            self.base_url += '://'
            if ':' in ip:
                self.base_url += ip
            else:
                self.base_url += '%s:%s' % (ip, port)

        self.base_url = self.base_url.rstrip('/')

    @defer.inlineCallbacks
    def disable(self):
        if not self._is_enabled:
            defer.returnValue(None)

        self._is_enabled = False

        self.site.stopFactory()
        self.torrent_handler.shutdown()
        self.thomas_http_output.stop()

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
    def stream_torrent(self, infohash=None, url=None, filedump=None, filepath_or_index=None, includes_name=False, wait_for_end_pieces=False, label=None, as_inline=False):
        logger.debug('Trying to stream infohash:%s, url:%s, filepath_or_index:%s' % (infohash, url, filepath_or_index))
        torrent = get_torrent(infohash)

        if torrent is None:
            logger.info('Did not find torrent, must add it')

            if not filedump and url:
                filedump = yield client.getPage(url)

            if not filedump:
                defer.returnValue({'status': 'error', 'message': 'unable to find torrent, provide infohash, url or filedump'})

            torrent_info = lt.torrent_info(lt.bdecode(filedump))
            infohash = str(torrent_info.info_hash())

            core = component.get("Core")
            try:
                yield core.add_torrent_file('file.torrent', base64.b64encode(filedump), {'add_paused': True})
                if label and 'Label' in component.get('CorePluginManager').get_enabled_plugins():
                    label_plugin = component.get('CorePlugin.Label')
                    if label not in label_plugin.get_labels():
                        label_plugin.add(label)

                    try:
                        label_plugin.set_torrent(infohash, label)
                    except:
                        logger.exception('Failed to set label')
            except:
                logger.exception('Failed to add torrent')
                defer.returnValue({'status': 'error', 'message': 'failed to add torrent'})

        if filepath_or_index is None:
            fn = ''
        elif isinstance(filepath_or_index, int):
            status = torrent.get_status(['files'])
            fn = status['files'][filepath_or_index]['path']
        else:
            fn = filepath_or_index

        try:
            stream_or_item = yield defer.maybeDeferred(self.torrent_handler.stream, infohash, fn, wait_for_end_pieces=wait_for_end_pieces)
            stream_url = self.thomas_http_output.serve_item(stream_or_item, as_inline=as_inline)
        except:
            logger.exception('Failed to stream torrent')
            defer.returnValue({'status': 'error', 'message': 'failed to stream torrent'})

        defer.returnValue({
            'status': 'success',
            'filename': stream_or_item.id,
            'use_stream_urls': self.config['use_stream_urls'],
            'auto_open_stream_urls': self.config['auto_open_stream_urls'],
            'url': '%s/streaming/%s' % (self.base_url, stream_url.lstrip('/'))
        })
