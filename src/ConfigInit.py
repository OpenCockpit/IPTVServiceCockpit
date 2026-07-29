# Copyright (C) 2026 by xcentaurix
# License: GNU General Public License v3.0


from Components.config import config, ConfigSubsection, ConfigSelection
from .Debug import log_levels, initLogging


class ConfigInit():
    def __init__(self):
        config.plugins.iptvservicecockpit = ConfigSubsection()
        config.plugins.iptvservicecockpit.debug_log_level = ConfigSelection(
            default="INFO", choices=list(log_levels.keys()))
        initLogging()
