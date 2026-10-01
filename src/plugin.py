# Copyright (C) 2026 by xcentaurix
# License: GNU General Public License v3.0


from Plugins.Plugin import PluginDescriptor
from .Debug import logger
from .Version import VERSION
from .IPTVServiceCockpit import IPTVServiceCockpit
from . import ConfigInit  # noqa: F401, pylint: disable=unused-import
from .M3U8Providers import installServiceExtensions, writeEPGImportConfig


def autoStart(reason, **__):
    if reason == 0:
        logger.info("+++ Version: %s starts...", VERSION)
        IPTVServiceCockpit()
    elif reason == 1:
        logger.info("--- shutdown")


def sessionStart(reason, session, **__):  # pylint: disable=unused-argument
    installServiceExtensions(session)
    # Runs after PlutoTVCockpit/RakutenTVCockpit/SamsungTVCockpit's own
    # sessionstart bouquet-building has had a chance to refresh their
    # channellist*.m3u8/xmltv.*.xml pairs - same best-effort timing the
    # channel-browsing menus themselves already rely on (they re-read these
    # files fresh on every visit regardless of when they were last written).
    writeEPGImportConfig()


def Plugins(**__):
    return [
        PluginDescriptor(
            where=[
                PluginDescriptor.WHERE_AUTOSTART
            ],
            fnc=autoStart,
            needsRestart=True
        ),
        PluginDescriptor(
            where=[
                PluginDescriptor.WHERE_SESSIONSTART
            ],
            fnc=sessionStart,
            needsRestart=True
        )
    ]
