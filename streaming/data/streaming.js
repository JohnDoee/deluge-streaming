/*
Script: streaming.js
    The client-side javascript code for the Streaming plugin.

Copyright:
    (C) John Doee 2009 <johndoee@tidalstream.org>
    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 3, or (at your option)
    any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, write to:
        The Free Software Foundation, Inc.,
        51 Franklin Street, Fifth Floor
        Boston, MA  02110-1301, USA.

    In addition, as a special exception, the copyright holders give
    permission to link the code of portions of this program with the OpenSSL
    library.
    You must obey the GNU General Public License in all respects for all of
    the code used other than OpenSSL. If you modify file(s) with this
    exception, you may extend this exception to your version of the file(s),
    but you are not obligated to do so. If you do not wish to do so, delete
    this exception statement from your version. If you delete this exception
    statement from all source files in the program, then also delete it here.
*/

PreferencePage = Ext.extend(Ext.Panel, {
    title: 'Streaming',
    border: false,
    layout: 'form',
    
    initComponent: function() {
        PreferencePage.superclass.initComponent.call(this);
        
        var om = this.optionsManager = new Deluge.OptionsManager();
        this.on('show', this.onPageShow, this);
        
        var fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            title: 'Streaming',
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelWidth: 110,
            defaultType: 'textfield',
            defaults: {
                width: 180,
            }
        });
        
        om.bind('port', fieldset.add({
            name: 'port',
            fieldLabel: _('Port'),
            decimalPrecision: 0,
            minValue: -1,
            maxValue: 99999
        }));
        
        om.bind('ip', fieldset.add({
            name: 'ip',
            fieldLabel: 'IP'
        }));
        
        om.bind('use_stream_urls', fieldset.add({
            xtype: 'checkbox',
            name: 'use_stream_urls',
            fieldLabel: 'Use StreamProtocol urls',
        }));
        
        om.bind('auto_open_stream_urls', fieldset.add({
            xtype: 'checkbox',
            name: 'auto_open_stream_urls',
            fieldLabel: 'AutoOpen StreamProtocol urls',
        }));
        
        om.bind('reset_complete', fieldset.add({
            xtype: 'checkbox',
            name: 'reset_complete',
            fieldLabel: 'Reset "do not download" when streamed file is complete',
        }));
        
        om.bind('allow_remote', fieldset.add({
            xtype: 'checkbox',
            name: 'allow_remote',
            fieldLabel: 'Allow remote control checkbox',
        }));
        
        om.bind('remote_username', fieldset.add({
            name: 'remote_username',
            fieldLabel: 'Remote username'
        }));
        
        om.bind('remote_password', fieldset.add({
            name: 'remote_password',
            inputType: 'password',
            fieldLabel: 'Remote password'
        }));
    },

    onApply: function() {
        var changed = this.optionsManager.getDirty();
        if (!Ext.isObjectEmpty(changed)) {
            deluge.client.streaming.set_config(changed, {
                success: this.onSetConfig,
                scope: this
            });
    
            for (var key in deluge.config) {
                deluge.config[key] = this.optionsManager.get(key);
            }
        }
    },
    
    onSetConfig: function() {
        this.optionsManager.commit();
    },
    
    onGotConfig: function(config) {
        this.optionsManager.set(config);
    },
    
    onPageShow: function() {
        deluge.client.streaming.get_config({
            success: this.onGotConfig,
            scope: this
        })
    }
});

StreamingPlugin = Ext.extend(Deluge.Plugin, {
    'name': 'Streaming',

	onDisable: function() {
        deluge.menus.filePriorities.remove('streamthis');
        
        deluge.preferences.selectPage(_('Plugins'));
        deluge.preferences.removePage(this.prefsPage);
        this.prefsPage.destroy();
	},

    onEnable: function() {
        this.prefsPage = new PreferencePage();
        deluge.preferences.addPage(this.prefsPage);
        
        console.log('Streaming plugin loaded');
        deluge.menus.filePriorities.addMenuItem({
            id: 'streamthis',
            text: 'Stream this file',
            iconCls: 'icon-down',
            handler: function (item, event) {
                deluge.menus.filePriorities.hide();
                var files = deluge.details.items.items[2];
                var nodes = files.getSelectionModel().getSelectedNodes();
                if (nodes) {
                    var fileIndex = nodes[0].attributes.fileIndex;
                    var tid = files.torrentId;
                    if (fileIndex >= 0) {
                        deluge.client.streaming.stream_torrent(tid, null, null, fileIndex, {
                            success: function (result) {
                                console.log('Got result', result);
                                if (result.status == 'success') {
                                    var url = result.url;
                                    if (result.use_stream_urls) {
                                        url = 'stream+' + url;
                                        if (result.auto_open_stream_urls) {
                                            window.location.assign(url);
                                            return;
                                        }
                                    }
                                    Ext.Msg.alert('Stream ready', 'URL for stream: <a target="_blank" href="' + url + '">' + url + '</a>');
                                } else {
                                    Ext.Msg.alert('Stream failed', 'Error message: ' + result.message);
                                }
                            }
                        })
                    }
                }
                return false;
            }
        });
	}
});
Deluge.registerPlugin('Streaming', StreamingPlugin);