# Copyright (C) 2026 by xcentaurix
# License: GNU General Public License v3.0


from .Debug import logger
from .M3U8Providers import installM3U8Providers


class IPTVServiceCockpit():
    def __init__(self):
        logger.info("...")
        installM3U8Providers()
