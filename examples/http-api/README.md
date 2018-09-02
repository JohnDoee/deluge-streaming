# HTTP API

Stream using the HTTP API built into Deluge Streaming.

## Requirements

* Python
* requests package

## Config

You need to enable HTTP API in Deluge Streaming config.

## Example

```python
from streamtorrent import stream_torrent

if __name__ == '__main__':
    with open('TPB.AFK.2013.1080p.h264-SimonKlose', 'rb') as f:
        torrent_data = f.read()

    # Stream 1080p TPB AFK using infohash to avoid posting the torrent
    # if it already exist.
    url = stream_torrent(
        'http://stream:password@127.0.0.1:46123/streaming/stream',
        infohash='411a7a164505636ab1a8276395b375a3a30bff32',
        torrent_body=torrent_data,
        label='tpbafk'
    )
    print('we can stream %s' % (url, ))
```

