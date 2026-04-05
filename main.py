import re
import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import json
from astrbot.api.provider import ProviderRequest

@register("astrbot_plugin_ncm_get", "AstrBot Developer", "解析网易云音乐链接，提取歌曲信息及完整歌词，并注入到上下文。", "1.0.0", "https://github.com/yourusername/astrbot_plugin_ncm_get")
class NcmGetPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 接收并使用配置参数，提供默认值
        self.auto_parse = self.config.get("auto_parse", True)
        self.cookie = self.config.get("cookie", "")
        # 恢复默认的注入模板包含 title, artist, lyrics
        self.inject_format = self.config.get("inject_format", "[系统附加信息] 用户分享了网易云音乐的歌曲《{title}》，歌手：{artist}。以下是完整歌词：\n{lyrics}\n\n请结合以上歌曲信息回复用户。")

    async def _fetch_song_detail(self, song_id: str):
        """获取歌曲名和歌手信息"""
        url = f"http://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://music.163.com/'
        }
        if self.cookie:
            headers['Cookie'] = self.cookie

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        #data = await resp.json()
                        raw_text = await resp.text()
                        data = json.loads(raw_text)
                        if data.get('songs') and len(data['songs']) > 0:
                            song = data['songs'][0]
                            title = song.get('name', '未知歌曲')
                            artists = "/".join([ar.get('name', '未知歌手') for ar in song.get('artists', [])])
                            return title, artists
        except Exception as e:
            logger.error(f"获取歌曲详情失败: {e}")
        return "未知歌曲", "未知歌手"

    async def _fetch_lyrics(self, song_id: str):
        """获取并清洗歌词"""
        url = f"http://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://music.163.com/'
        }
        if self.cookie:
            headers['Cookie'] = self.cookie

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as resp:
                    if resp.status == 200:
                        #data = await resp.json()
                        raw_text = await resp.text()
                        data = json.loads(raw_text)
                        if 'lrc' in data and 'lyric' in data['lrc']:
                            lyric_raw = data['lrc']['lyric']
                            # 使用正则去除歌词里的时间轴 [00:00.000]，让喂给大模型的文本更干净
                            clean_lyric = re.sub(r'\[\d{2}:\d{2}\.\d{2,3}\]', '', lyric_raw).strip()
                            # 过滤掉多余的空行
                            clean_lyric = re.sub(r'\n+', '\n', clean_lyric)
                            return clean_lyric if clean_lyric else "（纯音乐或无歌词）"
        except Exception as e:
            logger.error(f"获取歌词失败: {e}")
        return "未能获取到歌词"

    @filter.command("ncm_get")
    async def ncm_get(self, event: AstrMessageEvent, url: str):
        '''解析网易云音乐链接并获取歌曲详情'''
        try:
            song_id = self._extract_ncm_id(url)
            if song_id:
                # 异步获取信息
                title, artist = await self._fetch_song_detail(song_id)
                lyrics = await self._fetch_lyrics(song_id)
                
                iframe = f'<iframe frameborder="no" border="0" marginwidth="0" marginheight="0" width=330 height=86 src="//music.163.com/outchain/player?type=2&id={song_id}&auto=0&height=66"></iframe>'
                deeplink = f'orpheus://song/{song_id}/'
                
                msg = f"解析成功！\n歌曲：《{title}》- {artist}\n歌曲ID：{song_id}\n\n【歌词预览】\n{lyrics[:100]}...\n\niframe代码：\n{iframe}\nApp跳转链接：\n{deeplink}"
                yield event.plain_result(msg)
            else:
                yield event.plain_result("未识别到有效的网易云音乐链接或缺少歌曲ID。")
        except Exception as e:
            logger.error(f"解析链接出错: {e}")
            yield event.plain_result("解析链接时发生错误，请稍后再试。")

    @filter.command("ncm_cookie")
    async def ncm_cookie(self, event: AstrMessageEvent, cookie: str = ""):
        '''设置网易云音乐 Cookie'''
        if not cookie:
            yield event.plain_result("请提供有效的 Cookie 字符串。例如: /ncm_cookie MUSIC_U=xxxx;")
            return
            
        self.cookie = cookie
        # 这里仅做运行时更新。如果你想要持久化写入文件，需要结合 AstrBot 的 config 保存机制。
        yield event.plain_result(f"Cookie已更新成功！当前设置的 Cookie 长度为：{len(cookie)}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """监听 LLM 请求，自动拦截并解析网易云音乐链接"""
        try:
            if not self.auto_parse:
                return

            user_text = req.system_prompt + " " + event.message_str
            urls = re.findall(r'(https?://music\.163\.com[^\s]+)', user_text)
            
            if urls:
                url = urls[0] 
                song_id = self._extract_ncm_id(url)
                
                if song_id:
                    # 获取详细信息
                    title, artist = await self._fetch_song_detail(song_id)
                    lyrics = await self._fetch_lyrics(song_id)
                    
                    iframe = f'<iframe frameborder="no" border="0" marginwidth="0" marginheight="0" width=330 height=86 src="//music.163.com/outchain/player?type=2&id={song_id}&auto=0&height=66"></iframe>'
                    deeplink = f'orpheus://song/{song_id}/'
                    
                    # 格式化注入内容（传入所有可能的变量，避免 KeyError）
                    formatted_text = self.inject_format.format(
                        song_id=song_id,
                        iframe=iframe,
                        deeplink=deeplink,
                        title=title,
                        artist=artist,
                        lyrics=lyrics
                    )
                    
                    # 将解析出的信息追加到系统提示词中，供 LLM 参考
                    req.system_prompt += f"\n\n{formatted_text}"
                    logger.info(f"已成功拦截并注入网易云音乐链接信息: 《{title}》 ID:{song_id}")
                    for comp in event.message_obj.message:
                        if type(comp).__name__ == "Plain":
                            comp.text += f"\n\n[系统隐形备注：{title} 完整歌词：{lyrics}]"
                            break

        except Exception as e:
            logger.error(f"注入 LLM 上下文时发生错误: {e}")


    @filter.llm_tool(name="get_ncm_song_info")
    async def get_ncm_song_info(self, event: AstrMessageEvent, song_id: str):
        """
        获取网易云音乐歌曲的详细信息和完整歌词。当用户询问某首网易云歌曲的信息、歌词，或分享了歌曲链接时，调用此工具。
        
        Args:
            song_id (string): 网易云音乐的歌曲ID（纯数字，例如 1998355620）。如果用户提供的是完整链接，请提取其中的数字ID再调用。
        """
        logger.info(f"大模型触发了自主调用 Tool：尝试获取歌曲 ID {song_id}")
        try:
            # 清理可能的非数字字符（防止大模型误传带有链接的ID）
            clean_id = re.sub(r'\D', '', str(song_id))
            if not clean_id:
                return {"error": "无效的歌曲ID，请提供纯数字的歌曲ID。"}

            # 复用我们之前写好的异步获取方法
            title, artist = await self._fetch_song_detail(clean_id)
            lyrics = await self._fetch_lyrics(clean_id)
            
            iframe = f'<iframe frameborder="no" border="0" marginwidth="0" marginheight="0" width=330 height=86 src="//music.163.com/outchain/player?type=2&id={clean_id}&auto=0&height=66"></iframe>'
            deeplink = f'orpheus://song/{clean_id}/'
            
            # 将结构化数据返回给大模型
            return {
                "status": "success",
                "song_id": clean_id,
                "title": title,
                "artist": artist,
                "lyrics": lyrics,
                "iframe_code": iframe,
                "app_deeplink": deeplink
            }
        except Exception as e:
            logger.error(f"大模型调用 get_ncm_song_info 工具失败: {e}")
            return {
                "status": "error",
                "message": f"获取歌曲信息时发生异常：{str(e)}"
            }



    def _extract_ncm_id(self, url: str) -> str:
        """从网易云音乐链接中提取歌曲ID"""
        match = re.search(r'[?&]id=(\d+)', url)
        if match:
            return match.group(1)
        
        match = re.search(r'/song/(\d+)', url)
        if match:
            return match.group(1)
            
        return ""