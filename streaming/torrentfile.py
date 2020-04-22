import logging
import mimetypes
import os
import time
import threading

from io import BytesIO

from thomas import InputBase

logger = logging.getLogger(__name__)

PIECE_REQUEST_HISTORY_TIME = 10
MAX_PIECE_REQUEST_COUNT = 20

class DelugeTorrentInput(InputBase):
    plugin_name = 'torrent_file'
    protocols = []

    current_piece_data = None
    can_read_to = None
    last_available_piece = None
    _pos = None
    _closed = False

    def __init__(self, item, torrent_handler, infohash, offset, path):
        self.item = item
        self.torrent_handler = torrent_handler
        self.torrent = torrent_handler.get_torrent(infohash)
        self.infohash = infohash
        self.offset = offset
        self.path = path
        self.piece_buffer = {}
        self.requested_pieces = {}
        self.piece_consumption_time = []
        self.size, self.filename, self.content_type = self.get_info()

    def get_info(self):
        logger.info('Getting info about %r' % (self.path, ))

        content_type = mimetypes.guess_type(self.path)[0] or 'bytes'

        return self.item['size'], os.path.basename(self.path), content_type

    def ensure_exists(self):
        if not os.path.exists(self.path):
            self.torrent.can_read(self.offset)

    def tell(self):
        return self._pos

    def seek(self, pos):
        self.ensure_exists()
        self._pos = pos
        logger.debug('Seeking at %s torrentfile_id %r' % (self.tell(), id(self)))
        self.torrent.add_reader(self, self.item.path, self.offset + self.tell(), self.offset + self.size)

    def _read(self, num):
        data = self.current_piece_data.read(num)
        self._pos += len(data)
        return data

    def read(self, num):
        if self.current_piece_data:
            data = self._read(num)
            if data:
                return data

        self.ensure_exists()

        if self._pos is None:
            self.seek(0)

        logger.debug('Trying to read %s from %i torrentfile_id %r' % (self.path, self.tell(), id(self)))
        tell = self.tell()
        if self.can_read_to is None or self.can_read_to <= tell:
            can_read_result = self.torrent.can_read(self.offset + tell)
            self.last_available_piece = can_read_result[1]
            self.can_read_to = can_read_result[0] + tell

        current_piece, rest = self.current_piece
        logger.debug('Calculated last available piece is %s offset %s can_read_to %s piece_length %s' % (self.last_available_piece, self.offset, self.can_read_to, self.torrent.piece_length))

        while self.piece_consumption_time and self.piece_consumption_time[0] < time.time() - PIECE_REQUEST_HISTORY_TIME:
            self.piece_consumption_time.pop(0)

        max_piece_count = (self.last_available_piece - current_piece) + 1
        pieces_to_request = min(min(max(2, len(self.piece_consumption_time)), max_piece_count), MAX_PIECE_REQUEST_COUNT)

        logger.debug('New piece request status pieces_to_request: %s piece_consumption_time: %s max_piece_count: %s' % (pieces_to_request, len(self.piece_consumption_time), max_piece_count, ))
        logger.debug('Requested pieces: %r' % (self.requested_pieces.items()))
        logger.debug('Piece buffer: %r' % (self.piece_buffer.keys()))

        for piece in range(current_piece, current_piece + pieces_to_request):
            if piece in self.requested_pieces:
                continue

            logger.debug('Requesting piece %s' % (piece, ))
            self.requested_pieces[piece] = threading.Event()
            self.torrent.request_piece(piece)

        for _ in range(1000):
            if self.requested_pieces[current_piece].wait(1):
                break
            if self._closed:
                return b''
        else:
            return b''

        for delete_piece in [p for p in self.piece_buffer.keys() if p < current_piece]:
            del self.piece_buffer[delete_piece]

        for delete_piece in [p for p in self.requested_pieces.keys() if p < current_piece]:
            del self.requested_pieces[delete_piece]

        self.current_piece_data = self.piece_buffer[current_piece]
        self.current_piece_data.seek(rest)
        self.piece_consumption_time.append(time.time())
        logger.debug('Returning %s bytes' % (num, ))
        return self._read(num)

    @property
    def current_piece(self):
        from_byte = self.offset + self.tell()
        piece_length = self.torrent.piece_length
        piece, rest = divmod(from_byte, piece_length)
        return piece, rest

    def new_piece_available(self, piece, data):
        if piece not in self.requested_pieces or self.requested_pieces[piece].is_set():
            return

        logger.debug("Setting data for piece %s" % (piece, ))
        self.piece_buffer[piece] = BytesIO(data)
        self.requested_pieces[piece].set()

    def close(self):
        self.torrent.remove_reader(self)
        self._closed = True
