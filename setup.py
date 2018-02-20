#
# setup.py
#
# Copyright (C) 2015 John Doee <johndoee@tidalstream.org>
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

from setuptools import setup

__plugin_name__ = "Streaming"
__author__ = "Anders Jensen"
__author_email__ = "johndoee@tidalstream.org"
__version__ = "0.9.0"
__url__ = "https://github.com/JohnDoee/deluge-streaming"
__license__ = "GPLv3"
__description__ = "Enables streaming of files while downloading them."
__long_description__ = """This plugin adds a new entry to the file list context menu that enables
the user to stream a file using HTTP.

Technically, it tries to download the part of a file the user requests and
downloads ahead, this enables seeking in video files.

## How to use

* Install plugin
* Select a torrent
* Select _files_ tab
* Right-click a file.
* Click _Stream this file_
* Select the link, open it in a media player, e.g. VLC or MPC

If you want to stream from a non-local computer, e.g. your seedbox, you will need to change the IP in option to the external server ip."""
__pkg_data__ = {__plugin_name__.lower(): ["template/*", "data/*"]}

setup(
    name=__plugin_name__,
    version=__version__,
    description=__description__,
    author=__author__,
    author_email=__author_email__,
    url=__url__,
    license=__license__,
    long_description=__long_description__ if __long_description__ else __description__,

    packages=[__plugin_name__.lower()],
    package_data = __pkg_data__,

    entry_points="""
    [deluge.plugin.core]
    %s = %s:CorePlugin
    [deluge.plugin.gtkui]
    %s = %s:GtkUIPlugin
    [deluge.plugin.web]
    %s = %s:WebUIPlugin
    """ % ((__plugin_name__, __plugin_name__.lower())*3)
)
