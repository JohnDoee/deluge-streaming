# Streaming Plugin
https://github.com/JohnDoee/deluge-streaming

(c)2016 by Anders Jensen <johndoee@tidalstream.org>

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
make Deluge an abstraction layer for the [TidalStream](http://www.tidalstream.org/) project, i.e. torrents to http on demand.

The _allow remote_ option is to allow remote add and stream of torrents.

# Version Info

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
