"""管理台静态资源与响应压缩（v0.6.3，评审 P2-9）。

- ``/static/*``：base.html / agent.html 抽出的 CSS/JS。模板里用 ``static_url('admin/base.css')``
  生成带内容哈希的 URL（``?v=<sha256 前 12 位>``）；哈希匹配时返回
  ``Cache-Control: public, max-age=31536000, immutable``，否则 ``no-cache``（旧哈希 / 无哈希不长缓存）。
- ``SelectiveGZipMiddleware``：只压缩文本类响应（HTML/CSS/JS/JSON/CSV/SVG/XML/纯文本）。
  ``text/event-stream``（智能助手 SSE）、APK、GLB、图片、PDF 等一律原样透传：
  SSE 不能被压缩缓冲，APK 下载要保留 Content-Length / Range，二进制再压缩只浪费 CPU。
  流式 CSV 导出仍逐块写出（gzip 分块，去掉 Content-Length）。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATIC_DIR = Path(__file__).resolve().parent / "static"
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

COMPRESSIBLE_TYPES = frozenset({
    "text/html", "text/css", "text/plain", "text/csv", "text/javascript", "text/xml",
    "application/javascript", "application/json", "application/xml", "image/svg+xml",
})


@lru_cache(maxsize=None)
def asset_hash(rel_path: str) -> str:
    """文件内容哈希（进程内缓存：部署即换进程，文件不会在运行中变化）。"""
    path = (STATIC_DIR / rel_path).resolve()
    if STATIC_DIR not in path.parents or not path.is_file():
        raise FileNotFoundError(rel_path)
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def static_url(rel_path: str) -> str:
    rel_path = rel_path.lstrip("/")
    return f"/static/{rel_path}?v={asset_hash(rel_path)}"


class CachedStaticFiles(StaticFiles):
    """带内容哈希的请求长缓存（immutable），其余 no-cache（仍可用 ETag 304）。"""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            version = ""
            for part in scope.get("query_string", b"").decode("latin-1").split("&"):
                if part.startswith("v="):
                    version = part[2:]
            try:
                current = asset_hash(path.replace("\\", "/"))
            except (FileNotFoundError, OSError):
                current = None
            response.headers["Cache-Control"] = IMMUTABLE_CACHE if version and version == current else "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class _SelectiveGZipResponder(GZipResponder):
    async def send_with_gzip(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            await super().send_with_gzip(message)
            media_type = Headers(raw=message["headers"]).get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type not in COMPRESSIBLE_TYPES:
                # 复用父类「已有 Content-Encoding」分支：原样透传、逐块立即发送。
                self.content_encoding_set = True
            return
        await super().send_with_gzip(message)


class SelectiveGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 1000, compresslevel: int = 6) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "gzip" in headers.get("accept-encoding", "").lower() and "range" not in headers:
                responder = _SelectiveGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
                await responder(scope, receive, send)
                return
        await self.app(scope, receive, send)
