import os
import re
import sys
import xbmc
import json
import locale
import xbmcgui
import encodings
import xbmcaddon
import xbmcplugin
from lib.pysubs2.formats import FILE_EXTENSION_TO_FORMAT_IDENTIFIER

try:
    from urllib.parse import parse_qsl, unquote, urlencode
except ImportError:
    # noinspection PyUnresolvedReferences
    from urlparse import parse_qsl, unquote
    # noinspection PyUnresolvedReferences
    from urllib import urlencode

ADDON = xbmcaddon.Addon()

SUBTITLES_EXT = FILE_EXTENSION_TO_FORMAT_IDENTIFIER.keys()
EXT_RE = re.compile("({})$".format("|".join(SUBTITLES_EXT)), re.IGNORECASE)
LANG_RE = re.compile("[.-]([^.-]*)(?:[.-]forced)?\.([^.]+)$", re.IGNORECASE)

HEADER = "[" + ADDON.getAddonInfo("name") + "] original_sub <{}>"
HEADER_RE = re.compile("\[{}\] original_sub <(.+?)>".format(ADDON.getAddonInfo("name")))


def translate(text):
    return ADDON.getLocalizedString(text).encode("utf-8")


def get_active_players():
    command = '{"jsonrpc": "2.0", "method": "Player.GetActivePlayers", "id": 1}'
    return json.loads(xbmc.executeJSONRPC(command))["result"]


def get_subtitle_details():
    player_id = None
    active_players = get_active_players()
    for data in active_players:
        if data["type"] == "video":
            player_id = data["playerid"]
            break

    if player_id is None:
        return None

    command = ('{{"jsonrpc":"2.0","method":"Player.GetProperties",'
               '"params":{{"playerid":{},"properties":["subtitleenabled","currentsubtitle"]}},'
               '"id":1}}').format(player_id)

    data = json.loads(xbmc.executeJSONRPC(command))["result"]
    return data["currentsubtitle"] if data["subtitleenabled"] else None


def get_setting(name):
    command = ('{{"jsonrpc": "2.0", "id": 1, '
               '"method": "Settings.GetSettingValue", '
               '"params": {{"setting": "{}"}}}}').format(name)
    result = xbmc.executeJSONRPC(command)
    data = json.loads(result)

    if "result" in data and "value" in data["result"]:
        return data["result"]["value"]
    else:
        raise ValueError


def get_current_subtitle():
    if get_subtitle_details() is None:
        return None, None

    if get_setting("subtitles.storagemode") == 1:
        subtitle_path = get_setting("subtitles.custompath")
    else:
        subtitle_path = xbmc.getInfoLabel("Player.Folderpath")

    if not os.path.exists(subtitle_path):
        subtitle_path = xbmc.translatePath("special://temp")

    subtitle_lang = xbmc.getInfoLabel("VideoPlayer.SubtitlesLanguage")
    file_name = unquote(xbmc.getInfoLabel("Player.Filename"))
    file_name_base, _ = os.path.splitext(file_name)
    lang_index = len(file_name_base)

    subtitles = []
    for s in os.listdir(subtitle_path):
        if s.startswith(file_name_base):
            _s = s[lang_index:]
            match = LANG_RE.match(_s)
            if match:
                if xbmc.convertLanguage(match.group(1), xbmc.ISO_639_2) == subtitle_lang:
                    subtitles.append(os.path.join(subtitle_path, s))
            elif EXT_RE.match(_s) and not subtitle_lang:
                subtitles.append(os.path.join(subtitle_path, s))

    if len(subtitles) > 0:
        m_time = os.path.getmtime(subtitles[0])
        index = 0

        for i, sub in enumerate(subtitles, 1):
            _m_time = os.path.getmtime(sub)
            if _m_time > m_time:
                m_time = _m_time
                index = i

        return subtitles[index], subtitle_lang
    return None, subtitle_lang


def find_encoding_by_country(country):
    local_name = locale.locale_alias.get(country, None)
    if local_name:
        alias = local_name.split(".")[-1].lower()
        codec = encodings.search_function(alias)
        if codec:
            return codec.name

    return locale.getpreferredencoding()


def add_subtitle(handle, action, path, label, language):
    list_item = xbmcgui.ListItem(
        label=xbmc.convertLanguage(language, xbmc.ENGLISH_NAME),
        label2=label,
        iconImage="0",
        thumbnailImage=xbmc.convertLanguage(language, xbmc.ISO_639_1),
    )

    # list_item.setProperty("sync", "false")
    # list_item.setProperty("hearing_imp", "false")

    url = "plugin://{}/?{}".format(
        ADDON.getAddonInfo("id"),
        urlencode({"action": action, "path": path}))

    xbmcplugin.addDirectoryItem(
        handle=handle,
        url=url,
        listitem=list_item,
        isFolder=False,
    )


def list_subtitles(handle):
    path, lang = get_current_subtitle()
    add_subtitle(handle, "test", "path", path, "pt")


def run():
    # Make sure the manual search button is disabled
    if xbmc.getCondVisibility("Window.IsActive(subtitlesearch)"):
        window = xbmcgui.Window(10153)
        window.getControl(160).setEnableCondition(
            '!String.IsEqual(Control.GetLabel(100),"{}")'.format(
                ADDON.getAddonInfo("name")))

    if get_setting("subtitles.overrideassfonts"):
        xbmcgui.Dialog().notification(translate(32000), translate(32001))

    params = dict(parse_qsl(sys.argv[2][1:]))
    handle = int(sys.argv[1])

    if "action" in params and params["action"] in ["search", "manualsearch"]:
        list_subtitles(handle)

    xbmcplugin.endOfDirectory(handle)
