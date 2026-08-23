# Copyright (C) 2026 by xcentaurix
# License: GNU General Public License v3.0


import glob
import os
import re
import zlib
from xml.sax.saxutils import escape

from enigma import eServiceReference
from Screens.ChannelSelection import ChannelSelectionBase, ChannelSelectionEdit, MODE_TV

from .Debug import logger
from .__init__ import _


CHANNELLIST_GLOB = "/etc/enigma2/channellist*.m3u8"
TVG_ID_RE = re.compile(r'tvg-id="([^"]*)"', re.IGNORECASE)

# Arbitrary tag stashed in the eServiceReference path so patchedSetRoot() can
# recognise "enter one of our fake providers" without it ever being mistaken
# for a real 'FROM ...' eDVBDB query string.
PROVIDER_MARKER = "IPTVServiceCockpitM3U8Provider"

# Channel refs (live-browsed or favourited) never embed the actual stream URL:
# a favourite is a bouquet-file entry that outlives any given URL, and IPTV
# providers rotate/expire URLs routinely. Instead the ref encodes (provider
# m3u8 filename, tvg-id) and playServiceExtension() resolves that to today's
# URL at the moment of actual playback, by re-parsing the m3u8 file fresh -
# so a URL change in the file just works, whether the ref came from live
# browsing or from a bouquet the user favourited weeks ago. eServiceReference's
# own toString()/encode() escapes any literal ':' in the path automatically,
# so this needs no manual escaping to round-trip through a bouquet file.
CHANNEL_SCHEME = "iptvservicecockpit://"

# A channellist*.m3u8 entry whose URL is PlutoTVCockpit's own local PlutoProxy
# passthrough (http://<host>/auto/<channel_id>.m3u8 or /rec/<channel_id>.ts)
# isn't independently playable: PlutoProxy 404s that endpoint until
# PlutoRequest.buildStreamURL()/register_channel() has run for channel_id at
# least once, which normally only happens via PlutoTVCockpit's own
# "pluto://<channel_id>" scheme resolution. Enigma2's Navigation.playService()
# breaks out of the playServiceExtensions loop as soon as one hook changes the
# ref (see Navigation.py), so handing this off at *resolve* time would starve
# PlutoTVCockpit's own hook of its turn in that same call. Building the ref as
# "pluto://<channel_id>" up front instead - matching PlutoTVCockpit's own
# scheme exactly - means our playServiceExtension no-ops on it (it isn't
# CHANNEL_SCHEME) and PlutoTVCockpit's independently-registered hook handles
# registration+resolution entirely on its own, with no import dependency
# between the two plugins.
_PLUTO_PROXY_URL_RE = re.compile(r'/(?:auto|rec)/([0-9a-f]+)\.(?:m3u8|ts)$', re.IGNORECASE)
PLUTO_SCHEME = "pluto://"

_originalSetRoot = ChannelSelectionBase.setRoot
# addServiceToBouquet is defined on the ChannelSelectionEdit mixin, not
# ChannelSelectionBase itself (ChannelSelection combines both via multiple
# inheritance) - patching it on ChannelSelectionBase raises AttributeError
# at import time and takes the whole plugin down with it.
_originalAddServiceToBouquet = ChannelSelectionEdit.addServiceToBouquet


def providerName(filepath):
    name = os.path.basename(filepath)
    if name.lower().endswith(".m3u8"):
        name = name[:-len(".m3u8")]
    if name.lower().startswith("channellist"):
        name = name[len("channellist"):]
    name = name.strip("_- .").replace("_", " ").replace("-", " ").strip()
    return name.title() if name else _("Provider list")


def parseM3U(filepath):
    """Return [(name, tvg_id_or_None, url), ...] for filepath's current content."""
    channels = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f]
    except OSError as e:
        logger.error("could not read %s: %s", filepath, e)
        return channels
    name = None
    tvg_id = None
    for line in lines:
        if not line or line.startswith("#EXTM3U"):
            continue
        if line.startswith("#EXTINF"):
            name = line.rsplit(",", 1)[-1].strip()
            m = TVG_ID_RE.search(line)
            tvg_id = m.group(1) if m else None
        elif not line.startswith("#"):
            if name:
                channels.append((name, tvg_id, line))
            name = None
            tvg_id = None
    return channels


def isProviderRef(ref):
    return bool(ref) and ref.getPath().startswith(PROVIDER_MARKER + ":")


def buildProviderRef(filepath):
    ref = eServiceReference(eServiceReference.idDVB, eServiceReference.flagDirectory | eServiceReference.shouldSort, PROVIDER_MARKER + ":" + filepath)
    ref.setName(providerName(filepath))
    return ref


def addProviders(selection):
    for filepath in sorted(glob.glob(CHANNELLIST_GLOB)):
        selection.servicelist.addService(buildProviderRef(filepath))
    selection.servicelist.l.sort()


# onid distinct from PlutoTVCockpit's own native bouquet-building (which uses
# 0xFF, see PlutoTVDownload.py's _buildBouquetEntry) so this plugin's synthetic
# EPG identities can never collide with that plugin's real ones, even by hash
# accident - eEPGCache's uniqueEPGKey only compares (sid, onid, tsid), so a
# distinct onid alone guarantees the two never alias regardless of sid/tsid.
_EPG_ONID = 0xFE


def _synthEpgIds(path):
    """Stable per-channel (sid, tsid) derived from *path* (already unique per
    channel - see buildChannelRef), so every call for the same channel
    reproduces the exact same eEPGCache lookup key.

    eServiceReference(4097, 0, path) leaves data[1..3] (sid/tsid/onid) at
    zero (see iservice.h's path constructor), and eEPGCache's uniqueEPGKey
    (epgcache.h) keys events purely on those three fields, read straight off
    the ref - not the path, not the type. Every channel this plugin ever
    built therefore collapsed onto the identical (0, 0, 0) key: the actual
    cause of "epg doesn't work" - eEPGCache can't tell any of these channels
    apart, so imported events for one alias/overwrite another's.

    zlib.crc32 (not the builtin hash(), which is salted per-process since
    Python 3.3) keeps this deterministic across restarts, matching whatever
    a channels.xml generated from the same *path* expects.
    """
    h = zlib.crc32(path.encode("utf-8"))
    sid = (h & 0xFFFF) or 1  # 0 would mean "no service id" to some consumers
    tsid = (h >> 16) & 0xFFFF
    return sid, tsid


def buildChannelRef(provider_file, tvg_id, name, url):
    m = _PLUTO_PROXY_URL_RE.search(url)
    path = PLUTO_SCHEME + m.group(1) if m else CHANNEL_SCHEME + provider_file + "/" + (tvg_id or "-")
    sid, tsid = _synthEpgIds(path)
    # idServiceMP3 (gstreamer) playback, with a real per-channel sid/tsid so
    # eEPGCache can actually distinguish this channel's events from every
    # other one - see _synthEpgIds. Numeric prefix only in the string form,
    # path left empty there and set via setPath() afterward instead of
    # baking it into the constructor string: eServiceReferenceBase's parser
    # (service.cpp) treats any "://"-containing path as a URL and only
    # recognises a name after it if there's a space-delimited suffix: with
    # no such suffix (our case - name is set separately below), it silently
    # leaves *both* path and name empty rather than falling back to a plain
    # path. setPath()/setName() bypass that parser entirely - same pattern
    # resolveChannelRef already uses elsewhere in this file.
    sref = eServiceReference(f"4097:0:1:{sid:x}:{tsid:x}:{_EPG_ONID:x}:0:0:0:0:")
    sref.setPath(path)
    sref.setName(name)
    return sref


def fillChannels(selection, filepath):
    provider_file = os.path.basename(filepath)
    for name, tvg_id, url in parseM3U(filepath):
        selection.servicelist.addService(buildChannelRef(provider_file, tvg_id, name, url))
    selection.servicelist.l.sort()
    selection.servicelist.l.FillFinished()


def _parseChannelRef(sref):
    path = sref.getPath()
    if not path.startswith(CHANNEL_SCHEME):
        return None
    provider_file, _, tvg_id = path[len(CHANNEL_SCHEME):].partition("/")
    return provider_file, tvg_id


def resolveChannelRef(sref):
    """Rewrite a stable channel ref in place to today's real stream URL.

    Tries tvg-id first (stable across a provider's own URL rotations even
    if channels get reordered), falls back to matching the name that was
    showing when this ref was created/favourited (tvg-id isn't set by every
    playlist). Leaves sref untouched (and therefore unplayable) if the
    channel is simply gone from the file now - same honest failure mode as
    a DVB favourite whose transponder no longer carries that service.
    """
    parsed = _parseChannelRef(sref)
    if parsed is None:
        return
    provider_file, tvg_id = parsed
    filepath = os.path.join(os.path.dirname(CHANNELLIST_GLOB), os.path.basename(provider_file))
    name = sref.getName()
    channels = parseM3U(filepath)
    match = None
    if tvg_id != "-":
        match = next((url for _n, ch_tvg_id, url in channels if ch_tvg_id == tvg_id), None)
    if match is None:
        match = next((url for ch_name, _t, url in channels if ch_name == name), None)
    if match is None:
        logger.error("%s: no current entry for tvg_id=%s name=%s", provider_file, tvg_id, name)
        return
    sref.setPath(match)


def playServiceExtension(_nav, sref, *_args, **_kwargs):
    resolveChannelRef(sref)
    return sref, False


def recordServiceExtension(_nav, sref, *_args, **_kwargs):
    # Unlike PlutoTVCockpit's RecordingProxy, there's no local re-mux proxy
    # here to repoint to - a timer/instant-record needs the exact same
    # resolution as live playback, just against whatever real URL the m3u8
    # currently lists. recordServiceExtensions expects the bare sref back
    # (not the (sref, bool) tuple playServiceExtensions expects).
    resolveChannelRef(sref)
    return sref


def installServiceExtensions(session):
    if hasattr(session.nav, "playServiceExtensions") and playServiceExtension not in session.nav.playServiceExtensions:
        session.nav.playServiceExtensions.append(playServiceExtension)
        logger.info("registered playServiceExtension for %s refs", CHANNEL_SCHEME)
    if hasattr(session.nav, "recordServiceExtensions") and recordServiceExtension not in session.nav.recordServiceExtensions:
        session.nav.recordServiceExtensions.append(recordServiceExtension)
        logger.info("registered recordServiceExtension for %s refs", CHANNEL_SCHEME)


EPGIMPORT_DIR = "/etc/epgimport"
EPGIMPORT_SOURCES_FILE = "iptvservicecockpit.sources.xml"


def _matchingXmltvPath(provider_file):
    """PlutoTVCockpit/RakutenTVCockpit/SamsungTVCockpit each write their own
    channellist.<plugin>_<region>.m3u8 *and* a matching xmltv.<plugin>_
    <region>.xml (same tvg-ids) alongside it - that xmltv file is real EPG
    data already sitting right there, just never wired up to the refs this
    plugin hands out. Returns None for any provider file without one (a
    generic/foreign m3u8 with no known EPG source), so it's simply skipped.
    """
    if not provider_file.startswith("channellist."):
        return None
    candidate = "xmltv." + provider_file[len("channellist."):]
    candidate = os.path.splitext(candidate)[0] + ".xml"
    path = os.path.join(os.path.dirname(CHANNELLIST_GLOB), candidate)
    return path if os.path.isfile(path) else None


def writeEPGImportConfig():
    """(Re)generate EPGImport's source/channel-mapping files for every
    provider that has a matching Cockpit-generated xmltv.*.xml, so EPGImport
    feeds events into the exact (sid, onid, tsid) key buildChannelRef()
    assigns each channel (see buildChannelRef/_synthEpgIds).

    One channels.xml per provider, not a single shared one: the mapping key
    is the bare tvg-id string, and two unrelated providers coincidentally
    reusing the same tvg-id (plausible - they're arbitrary per-list slugs)
    would otherwise silently feed one channel's events to the other's.
    """
    # "sourcecat", not "source_cat" - EPGConfig.enumSourcesFile() matches that
    # exact tag name; anything else silently falls into EPGImportSources'
    # own hardcoded "no category" bucket (labelled literally "[.]" in its UI).
    sources = ['<?xml version="1.0" encoding="utf-8"?>\n<sources>\n<sourcecat sourcecatname="IPTVServiceCockpit">\n']
    provider_count = 0

    for filepath in sorted(glob.glob(CHANNELLIST_GLOB)):
        provider_file = os.path.basename(filepath)
        xmltv_path = _matchingXmltvPath(provider_file)
        if xmltv_path is None:
            continue

        channels_name = provider_file + ".channels.xml"
        lines = ['<?xml version="1.0" encoding="utf-8"?>\n<channels>\n']
        for name, tvg_id, url in parseM3U(filepath):
            if not tvg_id:
                continue
            ref = buildChannelRef(provider_file, tvg_id, name, url)
            lines.append(f'  <channel id="{escape(tvg_id.lower())}">{escape(ref.toCompareString())}</channel>\n')
        lines.append('</channels>\n')

        try:
            with open(os.path.join(EPGIMPORT_DIR, channels_name), "w", encoding="utf-8") as f:
                f.writelines(lines)
        except OSError as e:
            logger.error("could not write %s: %s", channels_name, e)
            continue

        sources.append(
            f'  <source type="gen_xmltv" nocheck="1" channels="{escape(channels_name)}">\n'
            f'    <description>{escape(providerName(filepath))}</description>\n'
            f'    <url>{escape(xmltv_path)}</url>\n'
            f'  </source>\n'
        )
        provider_count += 1

    sources.append('</sourcecat>\n</sources>\n')

    if not provider_count:
        return
    try:
        with open(os.path.join(EPGIMPORT_DIR, EPGIMPORT_SOURCES_FILE), "w", encoding="utf-8") as f:
            f.writelines(sources)
        logger.info("wrote %s (%d provider(s))", EPGIMPORT_SOURCES_FILE, provider_count)
    except OSError as e:
        logger.error("could not write %s: %s", EPGIMPORT_SOURCES_FILE, e)


def patchedSetRoot(self, root, justSet=False):
    if isProviderRef(root):
        # eListboxServiceContent.setRoot(root, justSet=True) only clears the
        # list and remembers root; it never asks eServiceCenter to resolve
        # our fake path, so we fill the channels ourselves instead.
        filepath = root.getPath().split(":", 1)[1]
        self.servicelist.setRoot(root, True)
        fillChannels(self, filepath)
        self.rootChanged = True
        # OpenViX names this buildTitleString(); OpenATV names it buildTitle().
        (self.buildTitleString if hasattr(self, "buildTitleString") else self.buildTitle)()
        return
    _originalSetRoot(self, root, justSet)
    if not justSet and self.mode == MODE_TV and "FROM PROVIDERS" in root.getPath():
        addProviders(self)


def patchedAddServiceToBouquet(self, dest, service=None):
    # On success (only) the stock implementation ends with
    # self.servicelist.resetRoot() to refresh the currently displayed list -
    # a native re-query of the current root against eServiceCenter. That's a
    # no-op restore for a real bouquet/provider root, but our provider root
    # isn't backed by eServiceCenter at all (see patchedSetRoot); resetRoot()
    # resolves it to nothing and leaves the list empty instead of the
    # channels fillChannels() put there. Re-fill it ourselves afterward
    # whenever we're still sat on one of our fake provider roots.
    _originalAddServiceToBouquet(self, dest, service)
    root = self.getRoot()
    if isProviderRef(root):
        filepath = root.getPath().split(":", 1)[1]
        fillChannels(self, filepath)


def installM3U8Providers():
    if ChannelSelectionBase.setRoot is not patchedSetRoot:
        ChannelSelectionBase.setRoot = patchedSetRoot
        logger.info("patched ChannelSelectionBase.setRoot for %s", CHANNELLIST_GLOB)
    if ChannelSelectionEdit.addServiceToBouquet is not patchedAddServiceToBouquet:
        ChannelSelectionEdit.addServiceToBouquet = patchedAddServiceToBouquet
        logger.info("patched ChannelSelectionEdit.addServiceToBouquet for %s", CHANNELLIST_GLOB)
