# Copyright (C) 2026 by xcentaurix
# License: GNU General Public License v3.0


from Components.config import config, ConfigSubsection


if not hasattr(config.plugins, "iptvservicecockpit"):
    config.plugins.iptvservicecockpit = ConfigSubsection()
