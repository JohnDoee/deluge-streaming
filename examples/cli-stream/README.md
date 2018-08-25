# Commandline Tool to stream

Stream from the commandline.

## Requirements

* Python
* deluge_client python package

## Installation example

```bash
virtualenv cli-example
cli-example/bin/pip install deluge_client
```

## Usage

Open a torrent directly in VLC on Linux or OSX.

```bash
vlc `cli-example/bin/python stream-cli.py username password my_video.torrent`
```