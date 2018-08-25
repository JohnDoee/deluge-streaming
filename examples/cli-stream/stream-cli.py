import argparse
import urllib

from deluge_client import DelugeRPCClient


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Stream something.')
    parser.add_argument('username', type=str, help='Deluge username')
    parser.add_argument('password', type=str, help='Deluge password')
    parser.add_argument('path_or_url', type=str, help='Path or URL to torrent')

    parser.add_argument('--hostname', '-o', type=str, default='localhost', help='Deluge daemon hostname or ip')
    parser.add_argument('--port', '-p', type=int, default=58846, help='Deluge daemon port')

    args = parser.parse_args()

    if args.path_or_url.startswith('http'):
        filedata = urllib.urlopen(args.path_or_url).read()
    else:
        with open(args.path_or_url, 'rb') as f:
            filedata = f.read()

    client = DelugeRPCClient(args.hostname, args.port, args.username, args.password)
    client.connect()

    result = client.streaming.stream_torrent(None, None, filedata, None, None, True)
    print(result['url'])
