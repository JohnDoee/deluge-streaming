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
    header: false,
    autoScroll: true,
    autoHeight: true,
    width: 320,
    _fields: {},

    initComponent: function() {
        PreferencePage.superclass.initComponent.call(this);

        var om = this.optionsManager = new Deluge.OptionsManager();
        this.on('show', this.onPageShow, this);

        var fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            title: 'Settings',
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelAlign: 'top',
            labelWidth: 150,
            width: 300,
            defaultType: 'textfield',
            defaults: {
                width: 280,
            }
        });

        om.bind('download_only_streamed', fieldset.add({
            xtype: 'checkbox',
            name: 'download_only_streamed',
            boxLabel: 'Download only streamed files, skip the other files',
        }));

        fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            title: 'File Serving Settings',
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelAlign: 'top',
            labelWidth: 150,
            width: 280,
            defaultType: 'textfield',
            defaults: {
                width: 260,
            }
        });

        om.bind('ip', fieldset.add({
            name: 'ip',
            fieldLabel: 'Hostname',
        }));

        om.bind('port', fieldset.add({
            name: 'port',
            fieldLabel: _('Port'),
            decimalPrecision: 0,
            minValue: -1,
            maxValue: 99999,
        }));

        var field = fieldset.add({
            xtype: 'togglefield',
            name: 'reverse_proxy_base_url',
            fieldLabel: 'Reverse Proxy Config',
        });

        om.bind('reverse_proxy_enabled', field.toggle);
        om.bind('reverse_proxy_base_url', field.input);

        fieldset = this.add({
			xtype: 'fieldset',
			border: false,
			autoHeight: true,
			defaultType: 'radio',
			style: 'margin-bottom: 5px; margin-top: 0; padding-bottom: 5px; padding-top: 0;',
			width: 280,
            labelWidth: 1
		});

        this._fields['serve_method_webui'] = fieldset.add({
            name: 'serve_method',
            boxLabel: 'Serve files via WebUI',
            inputValue: 'webui',
            disabled: true
        });

        om.bind('serve_method', this._fields['serve_method_webui']);

        this._fields['serve_method_standalone'] = fieldset.add({
            name: 'serve_method',
            boxLabel: 'Serve files via standalone',
            inputValue: 'standalone',
            disabled: true
        });
        om.bind('serve_method', this._fields['serve_method_standalone']);


        om.bind('use_ssl', fieldset.add({
            xtype: 'checkbox',
            name: 'use_ssl',
            boxLabel: 'Use SSL',
            style: 'margin-left: 12px;'
        }));

        fieldset = this.add({
			xtype: 'fieldset',
			border: false,
			autoHeight: true,
			defaultType: 'radio',
			style: 'margin-left: 24px; margin-bottom: 5px; margin-top: 0; padding-bottom: 5px; padding-top: 0;',
			width: 280,
            labelWidth: 1
		});

        this._fields['ssl_source_daemon'] = fieldset.add({
            name: 'ssl_source',
            boxLabel: 'Use Daemon/WebUI Certificate',
            inputValue: 'daemon',
            value: 'daemon'
        })
        om.bind('ssl_source', this._fields['ssl_source_daemon']);

        this._fields['ssl_source_custom'] = fieldset.add({
            name: 'ssl_source',
            boxLabel: 'Custom Certificate',
            inputValue: 'custom',
            value: 'custom'
        });
        om.bind('ssl_source', this._fields['ssl_source_custom']);

        fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            style: 'margin-left: 24px; margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelWidth: 110,
            defaultType: 'textfield',
            defaults: {
                width: 130,
            }
        });

        om.bind('ssl_priv_key_path', fieldset.add({
            name: 'ssl_priv_key_path',
            fieldLabel: 'Private key file path',
        }));

        om.bind('ssl_cert_path', fieldset.add({
            name: 'ssl_cert_path',
            fieldLabel: 'Certificate and chains file path',
        }));

        fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            title: 'Advanced settings',
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelAlign: 'top',
            labelWidth: 150,
            width: 280,
            defaultType: 'textfield',
            defaults: {
                width: 260,
            }
        });

        om.bind('allow_remote', fieldset.add({
            xtype: 'checkbox',
            name: 'allow_remote',
            boxLabel: 'Allow remote control',
            style: 'margin-left: 12px;',
            width: 150
        }));

        fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelAlign: 'top',
            labelWidth: 150,
            width: 260,
            defaultType: 'textfield',
            defaults: {
                width: 240,
            }
        });

        // om.bind('remote_username', fieldset.add({
        //     xtype: 'textfield',
        //     name: 'remote_username',
        //     fieldLabel: 'Remote control username'
        // }));

        om.bind('remote_password', fieldset.add({
            xtype: 'textfield',
            name: 'remote_password',
            fieldLabel: 'Remote control password'
        }));

        fieldset.add({
            xtype: 'textfield',
            id: 'remote_url',
            name: 'remote_url',
            readOnly: true,
            fieldLabel: 'Remote control url'
        });

        fieldset = this.add({
            xtype: 'fieldset',
            border: false,
            style: 'margin-bottom: 0px; padding-bottom: 0px; padding-top: 5px',
            autoHeight: true,
            labelWidth: 1,
            defaultType: 'textfield',
            defaults: {
                width: 200,
            }
        });

        om.bind('use_stream_urls', fieldset.add({
            xtype: 'checkbox',
            name: 'use_stream_urls',
            boxLabel: 'Use stream protocol urls',
            style: 'margin-left: 12px;'
        }));

        om.bind('auto_open_stream_urls', fieldset.add({
            xtype: 'checkbox',
            name: 'auto_open_stream_urls',
            boxLabel: 'Auto-open stream protocol urls',
            style: 'margin-left: 12px;'
        }));

        om.bind('aggressive_prioritizing', fieldset.add({
            xtype: 'checkbox',
            name: 'aggressive_prioritizing',
            boxLabel: 'Aggressive prioritizing',
            style: 'margin-left: 12px;'
        }));
    },

    onApply: function() {

        var changed = this.optionsManager.getDirty();
        for (var key in this._fields) {
            if (this._fields.hasOwnProperty(key)) {
                var v = this._fields[key];
                if (v.checked) {
                    changed[v.name] = v.inputValue;
                }
            }
        }
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

    onSetConfig: function(result) {
        this.optionsManager.commit();
        if (result) {
            var message_type = result[0];
            var message_class = result[1];
            var message = result[2];
            if (message_type == 'error') {
                var topic = 'Unknown error type'
                if (message_class == 'ssl') {
                    topic = 'SSL Failed'
                }
                Ext.Msg.alert(topic, message);
            }
        }
        this.updateRemoteUrl(this.optionsManager);
    },

    onGotConfig: function(config) {
        this.optionsManager.set(config);
        this.updateRemoteUrl(this.optionsManager);
    },

    onPageShow: function() {
        deluge.client.streaming.get_config({
            success: this.onGotConfig,
            scope: this
        })
    },

    updateRemoteUrl: function(optionsManager) {
        var apiUrl = 'http';
        if (optionsManager.get('use_ssl'))
            apiUrl += 's';
        apiUrl += '://' + optionsManager.get('remote_username') + ':' + optionsManager.get('remote_password') + '@' + optionsManager.get('ip') + ':' + optionsManager.get('port') + '/streaming/stream';
        Ext.getCmp('remote_url').setValue(apiUrl);
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
        var doStream = function (tid, fileIndex, asInline) {
            deluge.client.streaming.stream_torrent(tid, null, null, fileIndex, true, false, null, asInline, {
                success: function (result) {
                    if (result.status == 'success') {
                        if (asInline) {
                            window.open(result.url, '_blank');
                        } else {
                            var url = result.url;
                            if (result.use_stream_urls) {
                                url = 'stream+' + url;
                                if (result.auto_open_stream_urls) {
                                    window.location.assign(url);
                                    return;
                                }
                            }
                            Ext.Msg.alert('Stream ready', 'URL for stream: <a target="_blank" href="' + url + '">' + url + '</a>');
                        }
                    } else {
                        Ext.Msg.alert('Stream failed', 'Error message: ' + result.message);
                    }
                }
            })
        }

        var triggerStreamFile = function (asInline) {
            var files = deluge.details.items.items[2];
            var nodes = files.getSelectionModel().getSelectedNodes();
            if (nodes) {
                var fileIndex = nodes[0].attributes.fileIndex;
                var tid = files.torrentId;
                if (fileIndex >= 0) {
                    doStream(tid, fileIndex, asInline);
                }
            }
        }

        deluge.menus.filePriorities.addMenuItem({
            id: 'playthis',
            text: 'Play in browser',
            iconCls: 'icon-resume',
            handler: function (item, event) {
                deluge.menus.filePriorities.hide();
                triggerStreamFile(true);
                return false;
            }
        });

        deluge.menus.filePriorities.addMenuItem({
            id: 'streamthis',
            text: 'Stream this file',
            iconCls: 'icon-down',
            handler: function (item, event) {
                deluge.menus.filePriorities.hide();
                triggerStreamFile(false);
                return false;
            }
        });


        var triggerStreamTorrent = function (asInline) {
            var ids = deluge.torrents.getSelectedIds();
            if (ids) {
                doStream(ids[0], null, asInline);
            }
        }

        deluge.menus.torrent.addMenuItem({
            id: 'playthistorrent',
            text: 'Play in browser',
            iconCls: 'icon-resume',
            handler: function (item, event) {
                deluge.menus.torrent.hide();
                triggerStreamTorrent(true);
                return false;
            }
        });

        deluge.menus.torrent.addMenuItem({
            id: 'streamthistorrent',
            text: 'Stream this torrent',
            iconCls: 'icon-down',
            handler: function (item, event) {
                deluge.menus.torrent.hide();
                triggerStreamTorrent(false);
                return false;
            }
        });
	}
});
Deluge.registerPlugin('Streaming', StreamingPlugin);