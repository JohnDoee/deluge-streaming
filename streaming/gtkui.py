#
# gtkui.py
#
# Copyright (C) 2009 John Doee <johndoee@tidalstream.org>
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

import gtk

from deluge.log import LOG as log
from deluge.ui.client import client
from deluge.ui.gtkui import dialogs
from deluge.plugins.pluginbase import GtkPluginBase
import deluge.component as component
import deluge.common

from common import get_resource

class GtkUI(GtkPluginBase):
    def enable(self):
        self.glade = gtk.glade.XML(get_resource("config.glade"))

        component.get("Preferences").add_page("Streaming", self.glade.get_widget("prefs_box"))
        component.get("PluginManager").register_hook("on_apply_prefs", self.on_apply_prefs)
        component.get("PluginManager").register_hook("on_show_prefs", self.on_show_prefs)
        
        file_menu = component.get("MainWindow").main_glade.get_widget('menu_file_tab')
        
        self.sep = gtk.SeparatorMenuItem()
        self.item = gtk.MenuItem(_("_Stream this file"))
        self.item.connect("activate", self.on_menuitem_stream)
        
        file_menu.append(self.sep)
        file_menu.append(self.item)

        self.sep.show()
        self.item.show()

    def disable(self):
        component.get("Preferences").remove_page("Streaming")
        component.get("PluginManager").deregister_hook("on_apply_prefs", self.on_apply_prefs)
        component.get("PluginManager").deregister_hook("on_show_prefs", self.on_show_prefs)
        
        file_menu = component.get("MainWindow").main_glade.get_widget('menu_file_tab')

        file_menu.remove(self.item)
        file_menu.remove(self.sep)

    def on_apply_prefs(self):
        log.debug("applying prefs for Streaming")
        config = {
            "ip": self.glade.get_widget("input_ip").get_text(),
            "port": int(self.glade.get_widget("input_port").get_text()),
            "allow_remote": self.glade.get_widget("input_allow_remote").get_active(),
        }
        client.streaming.set_config(config)

    def on_show_prefs(self):
        client.streaming.get_config().addCallback(self.cb_get_config)

    def cb_get_config(self, config):
        "callback for on show_prefs"
        self.glade.get_widget("input_ip").set_text(config["ip"])
        self.glade.get_widget("input_port").set_text(str(config["port"]))
        self.glade.get_widget("input_allow_remote").set_active(config["allow_remote"])

    def on_menuitem_stream(self, data=None):
        torrent_id = component.get("TorrentView").get_selected_torrents()[0]
        
        ft = component.get("TorrentDetails").tabs['Files']
        paths = ft.listview.get_selection().get_selected_rows()[1]
        
        selected = []
        for path in paths:
            selected.append(ft.treestore.get_iter(path))
        
        def stream_ready(result):
            if result['status'] == 'success':
                dialogs.ErrorDialog('Stream ready', 'Copy the link into a media player', details=result['url']).run()
            else:
                dialogs.ErrorDialog('Stream failed', 'Was unable to prepare the stream', details=result).run()
        
        for select in selected:
            path = ft.get_file_path(select)
            client.streaming.stream_torrent(torrent_id, path).addCallback(stream_ready)
            break