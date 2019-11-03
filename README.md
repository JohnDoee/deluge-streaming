# Streaming Plugin
https://github.com/JohnDoee/deluge-streaming

(c)2019 by Anders Jensen <johndoee@tidalstream.org>

## Description

This plugin adds a new entry to the file list context menu that enables
the user to stream a file using HTTP.

Technically, it tries to download the part of a file the user requests and
downloads ahead, this enables seeking in video files.

## Where to download

You can download this release on Github. Look for the "releases" tab on the repository page.
Under that tab, eggs for Python 2.6 and 2.7 should exist.

## How to use

* Install plugin
* Select a torrent
* Select _files_ tab
* Right-click a file.
* Click _Stream this file_
* Select the link, open it in a media player, e.g. VLC or MPC

If you want to stream from a non-local computer, e.g. your seedbox, you will need to change the IP in option to the external server ip.

## Open directly in your video player

By using a small tool it is possible to it's possible to open streams directly in VLC or another media player.

* Download and install [StreamProtocol](http://streamprotocol.tidalstream.org/)
* Go into Deluge Streaming options and enable "Use stream protocol urls"
* Optional, if you want to skip the popup and open streams directly when ready, enable "Auto-open stream protocol urls"

## Motivation

The plugin is not meant to be used as a right-click to stream thing. The idea is to
make Deluge an abstraction layer for the [Tidalstream](http://www.tidalstream.org/) project, i.e. torrents to http on demand.

The _allow remote_ option is to allow remote add and stream of torrents.

## Todo

* [ ] Better feedback in interface about streams
* [ ] Better feedback when using API
* [ ] Fix problems when removing torrent from Deluge (sea of errors)

# Important Deluge 2 information

While developing the Deluge 2 version of this plugin I hit a few problems that might be visible for you too.

* When shutting down Deluge an exception / error happens every time, this bug is reported.
* Sometimes the Web UI does not load plugins correctly, try restarting Deluge and refresh your browser if this happens.

# HTTP API Usage

## Prerequisite

Install and enable the plugin. Afterwards, head into Streaming settings and enable "Allow remote control".
The URL found in the "Remote control url" field is where the API can be reached. The auth used is Basic Auth.

## Usage

There is only one end-point and that is where a torrent stream can be requested.

Both return the same responses and all responses are JSON encoded.
All successfully authenticated responses have status code 200.

## POST /streaming/stream

POST body must be the raw torrent you want to stream. No form formatting or anything can be used.

List of URL GET Arguments

* **path**: Path inside the torrent file to either a folder or a file you want to stream. The plugin will try to guess the best one. **Optional**. **Default**: '' (i.e. find the best file in the whole torrent)
* **infohash**: Infohash of the torrent you want to stream, can make it a bit faster as it can avoid reading POST body. **Optional**.
* **label**: If label plugin is enabled and the torrent is actually added then give the torrent this label. **Optional**. **Default**: ''
* **wait_for_end_pieces**: Wait for the first and last piece in the streamed file to be fully downloaded. Can be necessary for some video players. It also enforces that the torrent can be actually downloaded. If the key exist with any (even empty) value, the feature is enabled. **Optional**. **Default**: false

## GET /streaming/stream

* **infohash**: Does the same as when POSTed. **Mandatory**.
* **path**: Does the same as when POSTed. **Optional**.
* **wait_for_end_pieces**: Does the same as when POSTed. **Optional**.

## Success Response

```json
{
    "status": "success", # Always equals this
    "filename" "horse.mkv", # Filename of the streamed torrent
    "url": "http://example.com/" # URL where the file can be reached by e.g. a media player
}
```

## Error Response

```json
{
    "status": "error", # Always equals this
    "message" "Torrent failed" # description for why it failed
}
```

# Version Info

## Version 0.11.0
* Initial support for Deluge 2 / Python 3
* Added support for aggressive piece prioritization when it should not be necessary.
* Fixed bug related to paused torrent with no data downloaded.

## Version 0.10.5
* Added support for serving files inline

## Version 0.10.4
* Trying to set max priority less as it destroys performance

## Version 0.10.3
* Added label support
* Reverse proxy config / replace URL config
* Ensure internal Deluge state is updated before trying to use it

## Version 0.10.2
* Busting cache when waiting for piece
* Math error in calculating size of readable bytes

## Version 0.10.1
* Small bugfixes related to priorities, should actually make sequential download work.

## Version 0.10.0
* Rewrote large parts of the code
* Now using [thomas](https://github.com/JohnDoee/thomas) as file-reading core - this adds support for multi-rar streaming.
* Faster streaming by reading directly from disk
* Reverse proxy mode

## Version 0.9.0
* Few bugfixes
* Added support for Deluge 2

## Version 0.8.1
* Fixed some small problems and bugs
* better URL execution with GTKUI

## Version 0.8.0
* Improved remote control of streaming to make it work as originally intended.

## Version 0.7.1
* Trying to fix bug where piece buffer went empty
* Added support for SSL.

## Version 0.7.0
* Shrinked code by redoing queue algorithm. This should prevent more stalled downloads and allow it to act bittorrenty if necessary.
* Added support for waiting for end pieces to satisfy some video players (KODI)

## Version 0.6.1
* Should not have been in changelog: Fixed "resume on complete" broken-ness (i hope)

## Version 0.6.0
* Fixed URL encoding error
* Fixed "resume on complete" broken-ness (i hope)
* Changed default to not use stream urls

## Version 0.5.0
* Restructured the whole plugin
* Added support for StreamProtocol

## Version 0.4.1
* Fixed bug with old Deluge versions

## Version 0.4.0
* Added WebUI support
* Improved scheduling algorithm

## Version 0.3
* Fixed bug when streaming multiple files.
* Changed to try to prioritize end pieces more aggressively to not leave them hanging.
* Added option to download rest of torrent when finished downloading the streamed torrent.
* Added authentication to remote API.

## Version 0.2
* Improved buffering algorithm, not using only deadline anymore.

## Version 0.1
* Initial working release
