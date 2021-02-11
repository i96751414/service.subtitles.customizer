import codecs
import encodings
import json
import locale
import os
import re
import shutil
import sys

import xbmc
import xbmcaddon
import xbmcgui
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
EXT_RE = re.compile(r"({})$".format("|".join(SUBTITLES_EXT)), re.IGNORECASE)
LANG_RE = re.compile(r"[.-]([^.-]*)(?:[.-]forced)?\.([^.]+)$", re.IGNORECASE)


def execute_json_rpc(method, rpc_version="2.0", rpc_id=1, **params):
    return json.loads(xbmc.executeJSONRPC(json.dumps(dict(
        jsonrpc=rpc_version, method=method, params=params, id=rpc_id))))


def get_active_players():
    return execute_json_rpc("Player.GetActivePlayers")["result"]


def get_subtitle_details():
    player_id = None
    active_players = get_active_players()
    for data in active_players:
        if data["type"] == "video":
            player_id = data["playerid"]
            break

    if player_id is None:
        return None

    data = execute_json_rpc(
        "Player.GetProperties", playerid=player_id, properties=("subtitleenabled", "currentsubtitle"))["result"]
    return data["currentsubtitle"] if data["subtitleenabled"] else None


def get_setting(name):
    data = execute_json_rpc("Settings.GetSettingValue", setting=name)
    if "result" in data and "value" in data["result"]:
        return data["result"]["value"]
    raise ValueError("Unable to get setting " + name)


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

        for i in range(1, len(subtitles)):
            _m_time = os.path.getmtime(subtitles[i])
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
        self._header_re = re.compile(r"\[{}] original_sub <(.+?)>".format(self._name))

        self._subtitles_dir = os.path.normpath(os.path.join(
            xbmc.translatePath(self._addon.getAddonInfo("profile")), "subtitles"))
        if not os.path.exists(self._subtitles_dir):
            os.makedirs(self._subtitles_dir)

        self._handle = -1
        self._params = {}

    def _translate(self, text):
        return self._addon.getLocalizedString(text).encode("utf-8")

    def _add_subtitle(self, action, path, label, language):
        list_item = xbmcgui.ListItem(label=xbmc.convertLanguage(language, xbmc.ENGLISH_NAME), label2=label)
        list_item.setArt({"icon": "0", "thumb": xbmc.convertLanguage(language, xbmc.ISO_639_1)})
        # list_item.setProperty("sync", "false")
        # list_item.setProperty("hearing_imp", "false")
        url = "plugin://{}/?{}".format(self._id, urlencode(dict(action=action, path=path, language=language)))
        xbmcplugin.addDirectoryItem(self._handle, url, list_item)

    def _list_subtitles(self):
        current_path, lang = get_current_subtitle()
        if current_path is None:
            return

        xbmc.log("Current subtitle path is: " + current_path)
        if current_path.endswith(".ass"):
            with codecs.open(current_path, errors="ignore") as f:
                for line in f:
                    match = self._header_re.search(line)
                    if match:
                        current_path = match.group(1)
                        xbmc.log("Original subtitle path is: " + current_path)
                        break

        subtitle_path = os.path.join(self._subtitles_dir, os.path.basename(current_path))
        if current_path != subtitle_path:
            shutil.copy(current_path, subtitle_path)

        title = xbmc.getInfoLabel("Player.Title")
        self._add_subtitle("download", subtitle_path, "{} - {}".format(title, self._translate(32002)), lang)
        self._add_subtitle("convert", subtitle_path, "{} - {}".format(title, self._translate(32003)), lang)

    def _download_subtitle(self, path):
        list_item = xbmcgui.ListItem(label=path)
        xbmcplugin.addDirectoryItem(self._handle, path, list_item)

    def _convert_subtitle(self, path):
        dialog = xbmcgui.Dialog()
        if dialog.yesno(self._translate(32003), self._translate(32004)):
            self._addon.openSettings()

        name, _ = os.path.splitext(os.path.basename(path))
        converted = os.path.join(self._subtitles_dir, "{}_modified_{}.ass".format(self._name, name))

        lang = xbmc.getInfoLabel("VideoPlayer.SubtitlesLanguage")
        fps = float(xbmc.getInfoLabel("Player.Process(VideoFPS)"))
        encoding = find_encoding_by_country(xbmc.convertLanguage(lang, xbmc.ISO_639_1))

        subs = pysubs2.load(path, encoding, fps=fps)

        subs.styles["Default"].fontname = self._font_name
        subs.styles["Default"].fontsize = self._font_size
        subs.styles["Default"].primarycolor = self._primary_color
        subs.styles["Default"].secondarycolor = self._secondary_color
        subs.styles["Default"].tertiarycolor = self._tertiary_color
        subs.styles["Default"].outlinecolor = self._outline_color
        subs.styles["Default"].backcolor = self._back_color
        subs.styles["Default"].bold = False
        subs.styles["Default"].italic = False
        subs.styles["Default"].underline = False
        subs.styles["Default"].strikeout = False
        subs.styles["Default"].scalex = 100.0
        subs.styles["Default"].scaley = 100.0
        subs.styles["Default"].spacing = 0.0
        subs.styles["Default"].angle = 0.0
        subs.styles["Default"].borderstyle = self._border_style
        subs.styles["Default"].outline = self._outline_px
        subs.styles["Default"].shadow = self._shadow_px
        subs.styles["Default"].alignment = self._alignment
        subs.styles["Default"].marginl = self._margin_l
        subs.styles["Default"].marginr = self._margin_r
        subs.styles["Default"].marginv = self._margin_v
        subs.styles["Default"].encoding = 1

        subs.save(converted, encoding, fps=fps, header_notice=self._header.format(path))
        self._download_subtitle(converted)

    @property
    def _font_name(self):
        opt = self._addon.getSetting("font_name")
        if opt == "1":
            return "Teletext"
        return "Arial"

    @property
    def _font_size(self):
        return float(self._addon.getSetting("font_size"))

    @staticmethod
    def _get_color(color):
        if color == "0":
            return pysubs2.Color(0, 0, 0, 0)
        elif color == "1":
            return pysubs2.Color(255, 255, 255, 0)
        elif color == "2":
            return pysubs2.Color(255, 255, 0, 3)
        elif color == "3":
            return pysubs2.Color(0, 0, 255, 0)
        return pysubs2.Color(0, 0, 0, 255)

    @property
    def _primary_color(self):
        return self._get_color(self._addon.getSetting("primary_color"))

    @property
    def _secondary_color(self):
        return self._get_color(self._addon.getSetting("secondary_color"))

    @property
    def _tertiary_color(self):
        return self._get_color(self._addon.getSetting("tertiary_color"))

    @property
    def _outline_color(self):
        return self._get_color(self._addon.getSetting("outline_color"))

    @property
    def _back_color(self):
        return self._get_color(self._addon.getSetting("back_color"))

    @property
    def _margin_l(self):
        return int(self._addon.getSetting("margin_l"))

    @property
    def _margin_r(self):
        return int(self._addon.getSetting("margin_r"))

    @property
    def _margin_v(self):
        return int(self._addon.getSetting("margin_v"))

    @property
    def _border_style(self):
        opt = self._addon.getSetting("border_style")
        if opt == "0":
            return 1
        return 3

    @property
    def _outline_px(self):
        return float(self._addon.getSetting("outline_px"))

    @property
    def _shadow_px(self):
        return float(self._addon.getSetting("shadow_px"))

    @property
    def _alignment(self):
        return 3 * int(self._addon.getSetting("vertical_alignment")) + \
               int(self._addon.getSetting("horizontal_alignment")) + 1

    def run(self):
        # Make sure the manual search button is disabled
        if xbmc.getCondVisibility("Window.IsActive(subtitlesearch)"):
            window = xbmcgui.Window(10153)
            window.getControl(160).setEnableCondition('!String.IsEqual(Control.GetLabel(100),"{}")'.format(self._name))

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
