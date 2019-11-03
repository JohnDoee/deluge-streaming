import argparse

import requests

class FailedToStreamException(Exception):
    pass


def stream_torrent(remote_control_url, infohash=None, path=None, wait_for_end_pieces=True, label=None, torrent_body=None):
    """
    Add a torrent to deluge, stream it and return a URL to where it can be watched.

    All optional parameters are optional but you will need to at least provide an infohash (if the torrent is already added)
    or a torrent_body (if you want the torrent added).

    remote_control_url - The URL found in Deluge Streaming config
    infohash - Torrent infohash, makes it faster if the torrent is already added
    path - path inside the torrent you want to stream
    wait_for_end_pieces - make sure the first and last piece are downloaded before returning url.
                          This might be necessary for some players
    label - Label to set in deluge
    torrent_body - The content of the .torrent file you want to stream
    """
    first_part, second_part = remote_control_url.split('@')
    username, password = first_part.split('/')[2].split(':')
    url = '/'.join(first_part.split('/')[:2]) + '/' + second_part

    params = {}
    if infohash:
        params['infohash'] = infohash

    if wait_for_end_pieces:
        params['wait_for_end_pieces'] = wait_for_end_pieces

    if path:
        params['path'] = path

    if label:
        params['label'] = label

    if infohash: # try to stream it without posting torrent body first
        r = requests.get(url, auth=(username, password), params=params)
        if r.status_code != 200:
            raise FailedToStreamException('Got non-200 error code from Deluge')

        data = r.json()
        if data['status'] == 'success':
            return data['url']
        else:
            raise FailedToStreamException('Request failed: %r' % (data, ))

    if torrent_body:
        r = requests.post(url, auth=(username, password), params=params, data=torrent_body)
        if r.status_code != 200:
            raise FailedToStreamException('Got non-200 error code from Deluge')

        data = r.json()
        if data['status'] == 'success':
            return data['url']
        else:
            raise FailedToStreamException('Request failed: %r' % (data, ))

    raise FailedToStreamException('Streaming was never successful')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Stream some torrents")
    parser.add_argument('url', help="Full API Url including auth info")
    parser.add_argument('--infohash', nargs='?', help="Infohash of torrent to stream")
    parser.add_argument('--path', nargs='?', help="Path to file within the torrent to stream")
    parser.add_argument('--label', nargs='?', help="Label to add the torrent with")
    parser.add_argument('--torrent', nargs='?', help="Path to the torrent to stream", type=argparse.FileType(mode='rb'))
    parser.add_argument('--skip_wait_for_end_pieces', help="Wait until client downloaded the first and last piece of the torrent", action='store_false')


    args = parser.parse_args()

    kwargs = {
        'remote_control_url': args.url,
        'wait_for_end_pieces': args.skip_wait_for_end_pieces
    }

    if args.infohash:
        kwargs['infohash'] = args.infohash

    if args.path:
        kwargs['path'] = args.path

    if args.label:
        kwargs['label'] = args.label

    if args.torrent:
        kwargs['torrent_body'] = args.torrent.read()

    result = stream_torrent(**kwargs)
    print('URL %s' % (result, ))
