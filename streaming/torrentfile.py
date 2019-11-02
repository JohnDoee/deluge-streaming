import logging
import mimetypes
import os

from thomas import InputBase

logger = logging.getLogger(__name__)


class DelugeTorrentInput(InputBase.find_plugin('file')):
    plugin_name = 'torrent_file'
    protocols = []

    can_read_to = None

    def __init__(self, item, torrent_handler, infohash, offset, path):
        self.item = item
        self.torrent_handler = torrent_handler
        self.torrent = torrent_handler.get_torrent(infohash)
        self.infohash = infohash
        self.offset = offset
        self.path = path
        self.size, self.filename, self.content_type = self.get_info()

    def get_info(self):
        logger.info('Getting info about %r' % (self.path, ))

        content_type = mimetypes.guess_type(self.path)[0] or 'bytes'

        return self.item['size'], os.path.basename(self.path), content_type

    def ensure_exists(self):
        if not os.path.exists(self.path):
            self.torrent.can_read(self.offset)

    def seek(self, pos):
        self.ensure_exists()
        super(DelugeTorrentInput, self).seek(pos)
        logger.debug('Seeking at %s torrentfile_id %r' % (self.tell(), id(self)))
        self.torrent.add_reader(self, self.item.path, self.offset + self.tell(), self.offset + self.size)

    def read(self, num):
        self.ensure_exists()

        if not self._open_file:
            self.seek(0)

        logger.debug('Trying to read %s from %i torrentfile_id %r' % (self.path, self.tell(), id(self)))
        tell = self.tell()
        if self.can_read_to is None or self.can_read_to <= tell:
            self.can_read_to = self.torrent.can_read(self.offset + tell) + tell

            if self._open_file:
                self._open_file.seek(tell)

        real_num = min(num, self.can_read_to - tell)
        if num != real_num:
            logger.info('The real number we can read to is %s and not %s at position %s' % (real_num, num, tell))

        if not self._open_file: # the file was closed while we waited
            return b''

        data = super(DelugeTorrentInput, self).read(real_num)
        return data

    def close(self):
        self.torrent.remove_reader(self)
        super(DelugeTorrentInput, self).close()
