# Streaming Plugin
https://github.com/JohnDoee/deluge-streaming

(c)2015 by Anders Jensen <johndoee@tidalstream.org>

## Description

This plugin adds a new entry to the file list context menu that enables
the user to stream a file using HTTP.

Technically, it tries to download the part of a file the user requests and
downloads ahead, this enables seeking in video files.

## How to use

* Install plugin
* Select a torrent
* Select _files_ tab
* Right-click a file.
* Click _Stream this file_
* **WAIT**, it will try to buffer the first pieces of the file before generating a link (no feedback yet).
* Select the link, open it in a media player, e.g. VLC or MPC

If you want to stream from a non-local computer, e.g. your seedbox, you will need to change the IP in option to the external server ip.

## Motivation

The plugin is not meant to be used as a right-click to stream thing. The idea is to
make Deluge an abstraction layer for the [TidalStream](http://www.tidalstream.org/) project, i.e. torrents to http on demand.

The _allow remote_ option is to allow remote add and stream of torrents.

## ToDo

* Add authentication to the API
* Add support for the WebUI
* There are a few situations where an uncaught exception is thrown.
* Add feedback when preparing stream.

# Version Info

## Version 0.3
* Fixed bug when streaming multiple files.
* Changed to try to prioritize end pieces more aggressively to not leave them hanging.
* Added option to download rest of torrent when finished downloading the streamed torrent.
* Added authentication to remote API.

## Version 0.2
* Improved buffering algorithm, not using only deadline anymore.

## Version 0.1
* Initial working release