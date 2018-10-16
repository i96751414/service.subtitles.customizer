import os
import re
import sys
import xbmc
import json
import locale
import codecs
import xbmcgui
import encodings
import xbmcaddon
import xbmcplugin
from lib import pysubs2
from lib.pysubs2.formats import FILE_EXTENSION_TO_FORMAT_IDENTIFIER

try:
    from urllib.parse import parse_qsl, unquote, urlencode
except ImportError:
    # noinspection PyUnresolvedReferences
    from urlparse import parse_qsl, unquote
    # noinspection PyUnresolvedReferences
    from urllib import urlencode

SUBTITLES_EXT = FILE_EXTENSION_TO_FORMAT_IDENTIFIER.keys()
EXT_RE = re.compile("({})$".format("|".join(SUBTITLES_EXT)), re.IGNORECASE)
LANG_RE = re.compile("[.-]([^.-]*)(?:[.-]forced)?\.([^.]+)$", re.IGNORECASE)


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


class Customizer(object):
    def __init__(self):
        self._addon = xbmcaddon.Addon()
        self._name = self._addon.getAddonInfo("name")
        self._id = self._addon.getAddonInfo("id")

        self._header = u"[" + self._name + "] original_sub <{}>"
        self._header_re = re.compile("\[{}\] original_sub <(.+?)>".format(self._name))

        self._subtitles_dir = os.path.normpath(os.path.join(
            xbmc.translatePath(self._addon.getAddonInfo("profile")),
            "subtitles"))
        if not os.path.exists(self._subtitles_dir):
            os.makedirs(self._subtitles_dir)

        self._handle = -1
        self._params = {}

    def _translate(self, text):
        return self._addon.getLocalizedString(text).encode("utf-8")

    def _add_subtitle(self, action, path, label, language):
        list_item = xbmcgui.ListItem(
            label=xbmc.convertLanguage(language, xbmc.ENGLISH_NAME),
            label2=label,
            iconImage="0",
            thumbnailImage=xbmc.convertLanguage(language, xbmc.ISO_639_1),
        )

        # list_item.setProperty("sync", "false")
        # list_item.setProperty("hearing_imp", "false")

        url = "plugin://{}/?{}".format(
            self._id,
            urlencode({"action": action, "path": path}))

        xbmcplugin.addDirectoryItem(
            handle=self._handle,
            url=url,
            listitem=list_item,
            isFolder=False,
        )

    def _list_subtitles(self):
        path, lang = get_current_subtitle()
        if path is None:
            return

        if path.endswith(".ass"):
            with codecs.open(path, errors="ignore") as f:
                for line in f:
                    match = self._header_re.search(line)
                    if match:
                        path = match.group(1)
                        break

        title = xbmc.getInfoLabel("Player.Title")
        self._add_subtitle(
            "download", path, "{} - {}".format(title, self._translate(32002)), lang)
        self._add_subtitle(
            "convert", path, "{} - {}".format(title, self._translate(32003)), lang)

    def _download_subtitle(self, path):
        list_item = xbmcgui.ListItem(label=path)
        xbmcplugin.addDirectoryItem(
            handle=self._handle,
            url=path,
            listitem=list_item,
            isFolder=False,
        )

    def _convert_subtitle(self, path):
        name, _ = os.path.splitext(os.path.basename(path))
        converted = os.path.join(self._subtitles_dir, "{}_modified_{}.ass".format(
            self._name, name))

        lang = xbmc.getInfoLabel("VideoPlayer.SubtitlesLanguage")
        fps = float(xbmc.getInfoLabel("Player.Process(VideoFPS)"))
        encoding = find_encoding_by_country(
            xbmc.convertLanguage(lang, xbmc.ISO_639_1))

        subs = pysubs2.load(path, encoding, fps=fps)
        subs.save(converted, encoding, fps=fps, header_notice=self._header.format(path))
        self._download_subtitle(converted)

    def run(self):
        # Make sure the manual search button is disabled
        if xbmc.getCondVisibility("Window.IsActive(subtitlesearch)"):
            window = xbmcgui.Window(10153)
            window.getControl(160).setEnableCondition(
                '!String.IsEqual(Control.GetLabel(100),"{}")'.format(
                    self._name))

        if get_setting("subtitles.overrideassfonts"):
            xbmcgui.Dialog().notification(self._translate(32000), self._translate(32001))

        self._handle = int(sys.argv[1])
        self._params = dict(parse_qsl(sys.argv[2][1:]))

        if "action" in self._params:
            if self._params["action"] in ["search", "manualsearch"]:
                self._list_subtitles()
            elif self._params["action"] == "download" and "path" in self._params:
                self._download_subtitle(self._params["path"])
            elif self._params["action"] == "convert" and "path" in self._params:
                self._convert_subtitle(self._params["path"])

        xbmcplugin.endOfDirectory(self._handle)
