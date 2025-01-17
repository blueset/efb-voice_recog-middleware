# coding: utf-8
import base64
import logging
import os
import tempfile
import requests
from tempfile import NamedTemporaryFile
from typing import IO, Any, Dict, Optional, List, BinaryIO

import yaml
import pydub
import shutil

from ehforwarderbot import EFBMiddleware, EFBMsg, MsgType, EFBChat
from ehforwarderbot.utils import get_config_path
from . import __version__ as version
from abc import ABC, abstractmethod


class VoiceRecogMiddleware(EFBMiddleware):
    """
    EFB Middleware - Voice recognize middleware
    Convert voice mesage replied by user to text message.
    Author: Catbaron <https://github.com/catbaron>
    """

    middleware_id: str = "catbaron.voice_recog"
    middleware_name: str = "Voice Recognition Middle"
    __version__ = version.__version__
    logger: logging.Logger = logging.getLogger(
        "plugins.%s.VoiceRecogMIddleware" % middleware_id)

    voice_engines: List = []

    def __init__(self, instance_id: str = None):
        super().__init__()
        self.config: Dict[str: Any] = self.load_config()
        tokens: Dict[str, Any] = self.config.get("speech_api", dict())
        self.lang: str = self.config.get('language', 'zh')

        if "baidu" in tokens:
            self.voice_engines.append(
                BaiduSpeech(channel=1, key_dict=tokens['baidu'])
                )

    def load_config(self) -> Optional[Dict]:
        config_path: str = get_config_path(self.middleware_id)
        if not os.path.exists(config_path):
            self.logger.info('The configure file does not exist!')
            return
        with open(config_path, 'r') as f:
            d: Dict[str, str] = yaml.load(f)
            if not d:
                self.logger.info('Load configure file failed!')
                return
            return d

    def recognize(self, file: BinaryIO, lang: str) -> List[str]:
        '''
        Recognize the audio file to text.
        Args:
            file: An andio file. It should be FILE object in 'rb'
                  mode or string of path to the audio file.
        '''
        results = [f'{e.engine_name} ({lang}): {e.recognize(file, lang)}'
                   for e in self.voice_engines]
        return results

    @staticmethod
    def sent_by_master(message: EFBMsg) -> bool:
        author: EFBChat = message.author
        try:
            if author.module_id == 'blueset.telegram':
                return True
            else:
                return False
        except Exception:
            return False

    def process_message(self, message: EFBMsg) -> Optional[EFBMsg]:
        """
        Process a message with middleware
        Args:
            message (:obj:`.EFBMsg`): Message object to process
        Returns:
            Optional[:obj:`.EFBMsg`]: Processed message or None if discarded.
        """
        if self.sent_by_master(message) or message.type != MsgType.Audio:
            return message

        audio: BinaryIO = NamedTemporaryFile()
        shutil.copyfileobj(message.file, audio)
        audio.file.seek(0)
        message.file.file.seek(0)
        try:
            reply_text: str = '\n'.join(self.recognize(audio, self.lang))
        except Exception:
            message.text += 'Failed to recognize voice content.'
            return message
        message.text += reply_text
        return message


class SpeechEngine(ABC):
    """Name of the speech recognition engine"""
    engine_name: str = __name__
    """List of languages codes supported"""
    lang_list: List[str] = []

    @abstractmethod
    def recognize(self, file: IO[bytes], lang: str):
        raise NotImplementedError()


class BaiduSpeech(SpeechEngine):
    key_dict: Dict[str, str] = None
    access_token: str = None
    full_token = None
    engine_name: str = "Baidu"
    lang_list = ['zh', 'ct', 'en']

    def __init__(self, channel: int, key_dict: Dict[str, str]):
        self.channel = channel
        self.key_dict = key_dict
        d = {
            "grant_type": "client_credentials",
            "client_id": key_dict['api_key'],
            "client_secret": key_dict['secret_key']
        }
        r = requests.post(
            "https://openapi.baidu.com/oauth/2.0/token",
            data=d
            ).json()
        self.access_token: str = r['access_token']
        self.full_token = r

    def recognize(self, file, lang):
        if hasattr(file, 'read'):
            pass
        elif isinstance(file, str):
            file = open(file, 'rb')
        else:
            return [
                "ERROR!", 
                "File must be a path string or a file object in `rb` mode."
                ]
        if lang.lower() not in self.lang_list:
            return ["ERROR!", "Invalid language."]

        audio = pydub.AudioSegment.from_file(file)
        audio = audio.set_frame_rate(16000)
        d = {
            "format": "pcm",
            "rate": 16000,
            "channel": self.channel,
            "cuid": "testing_user",
            "token": self.access_token,
            "lan": lang,
            "len": len(audio.raw_data),
            "speech": base64.b64encode(audio.raw_data).decode()
        }
        r = requests.post("http://vop.baidu.com/server_api", json=d)
        rjson = r.json()
        if rjson['err_no'] == 0:
            return '\n'.join(rjson['result'])
        else:
            return ["ERROR!", rjson['err_msg']]


class BingSpeech(SpeechEngine):
    keys = None
    access_token = None
    engine_name = "Bing"
    lang_list = ['ar-EG', 'de-DE', 'en-US', 'es-ES', 'fr-FR',
                 'it-IT', 'ja-JP', 'pt-BR', 'ru-RU', 'zh-CN']

    @staticmethod
    def first(data, key):
        """
        Look for first element in a list that matches a criteria.

        Args:
            data (list): List of elements
            key (function with one argument that returns Boolean value):
                Function to decide if an element matches the criteria.

        Returns:
            The first element found, or ``None``.
        """
        for i in data:
            if key(i):
                return i
        return None

    def __init__(self, channel, keys):
        self.channel = channel
        self.keys = keys

    def recognize(self, path, lang):
        if isinstance(path, str):
            file = open(path, 'rb')
        else:
            return ["ERROR!", "File must be a path string."]
        if lang not in self.lang_list:
            lang = self.first(self.lang_list, lambda a: a.split('-')[0] == lang.split('-')[0])
            if lang not in self.lang_list:
                return ["ERROR!", "Invalid language."]

        with tempfile.NamedTemporaryFile() as f:
            audio = pydub.AudioSegment.from_file(file)
            audio = audio.set_frame_rate(16000)
            audio.export(f.name, format="wav")
            header = {
                "Ocp-Apim-Subscription-Key": self.keys,
                "Content-Type": "audio/wav; samplerate=16000"
            }
            d = {
                "language": lang,
                "format": "detailed",
            }
            f.seek(0)
            r = requests.post("https://speech.platform.bing.com/speech/recognition/conversation/cognitiveservices/v1",
                              params=d, data=f.read(), headers=header)

            try:
                rjson = r.json()
            except ValueError:
                return ["ERROR!", r.text]

            if r.status_code == 200:
                return [i['Display'] for i in rjson['NBest']]
            else:
                return ["ERROR!", r.text]
