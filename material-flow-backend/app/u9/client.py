"""U9 客户端。

  - U9CClient：U9 Cloud（U9C）REST /webapi，已按用户提供的 swagger 导出实现（只读查询）。
  - ClassicU9Client：经典 U9（UBF WebService/SOAP）占位，调用抛 U9NotImplemented。

U9C 约定：
  - 授权：GET {base}/webapi/OAuth2/AuthLogin?clientid=&clientsecret=&userCode=&entcode=&orgcode=
    → {"ResCode":0, "Data":"<token>"}；业务接口请求头带 token。
  - 业务接口统一返回 {ResCode(0=成功), Success, ResMsg, Data}。
  - token 进程内缓存（U9_TOKEN_TTL_SECONDS，默认 50 分钟），业务接口遇到授权失效自动重登一次。

网络层只用标准库 urllib（生产环境 venv 未装 httpx/requests，避免部署时额外 pip）。
测试通过构造参数 transport 注入假的 HTTP 层。
凭证绝不写日志、不进异常文本（异常里只出现接口路径，不含查询串）。
"""
from __future__ import annotations

import json
import re
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable

from . import mappers
from .config import U9Config

log = logging.getLogger("app.u9")

# transport(method, url, headers, body_bytes|None, timeout, verify_ssl) -> (http_status, body_bytes)
Transport = Callable[[str, str, dict, "bytes | None", float, bool], "tuple[int, bytes]"]

PATH_ITEMS = "/webapi/ItemMaster/Query"
PATH_BOM = "/webapi/BOM/Query"
PATH_INVENTORY = "/webapi/Invtrans/QueryQohAndAvailable"
PATH_MO = "/webapi/MODoc/Query"
PATH_SQL = "/webapi/QueryCommon/QueryInfoBySql"
# 拼进 SQL 的单号/组织编码只允许这些字符，杜绝注入
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-.]{1,60}$")
# 料号/单号拼进固定 SELECT 的字符串字面量：现场料号含 +（如 30201001-00001+A）和中文，
# 只拒绝能打破字面量或注释的字符（引号、反斜杠、分号、控制字符、-- 与 /*），不加 N 前缀（U9 接口会把 SQL 转小写，n'' 非法）。
_CODE_KEY = re.compile(r"^[^'\"\\;\x00-\x1f\x7f]{1,80}$")


def _code_ok(code: str) -> bool:
    return bool(code) and bool(_CODE_KEY.match(code)) and "--" not in code and "/*" not in code

AUTH_ERRORS = {
    401: "未授权：clientid / clientsecret 不正确，或该应用未在 U9C「第三方应用授权」中启用",
    405: "授权码已过期",
    406: "用户名不存在（U9_USER_CODE）",
    408: "组织编码不存在（U9_ORG_CODE）",
    601: "客户端 IP 未授权：请在 U9C「第三方应用授权」中把本服务器出口 IP 加入白名单",
}
# 业务接口返回这些 ResCode 视为 token 失效，重登后重试一次
TOKEN_INVALID_CODES = {401, 405}
CLR_HINT = ("（U9 服务器端 SQL CLR 程序集 ufida.u9.sql.clrlib 加载失败，属于 U9 数据库服务器问题，"
            "需 U9 实施/运维处理 SQL Server 的 CLR 信任配置；在此之前请按料号查询库存）")


class U9Error(Exception):
    """U9 调用失败（网络/认证/业务校验）。message 为中文，可直接展示。"""

    def __init__(self, message: str, *, res_code: Any = None, http_status: int | None = None):
        super().__init__(message)
        self.res_code = res_code
        self.http_status = http_status


class U9AuthError(U9Error):
    """授权失败（clientid/secret、用户、组织、IP 白名单）。"""


class U9NotImplemented(U9Error):
    """接口占位：经典 U9 待实施商提供 WSDL/字段文档后实现。"""


class U9DeadlineExceeded(U9Error):
    """单次同步超过 U9_SYNC_MAX_SECONDS，主动中止（避免长时间占用服务）。"""


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                      timeout: float, verify_ssl: bool) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    ctx = None
    if url.lower().startswith("https"):
        ctx = ssl.create_default_context()
        if not verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:  # noqa: BLE001
            data = b""
        return e.code, data


# token 缓存：同一套连接参数共享（进程内）
_TOKEN_CACHE: dict[tuple, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()


def clear_token_cache() -> None:
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _dedupe(codes: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for c in codes or []:
        c = (c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


class U9Client:
    """接口约定（两种实现共用）。"""

    def __init__(self, cfg: U9Config, transport: Transport | None = None):
        self.cfg = cfg
        self.transport = transport or _urllib_transport

    def health_check(self) -> dict:
        raise U9NotImplemented("health_check 未实现")

    def fetch_items(self, codes: list[str]) -> list[dict]:
        raise U9NotImplemented("fetch_items 未实现")

    def fetch_bom(self, product_code: str) -> dict:
        raise U9NotImplemented("fetch_bom 未实现")

    def fetch_inventory(self, item_codes: list[str] | None = None, wh: str | None = None,
                        errors: list[dict] | None = None) -> list[dict]:
        raise U9NotImplemented("fetch_inventory 未实现")

    def fetch_production_orders(self, doc_nos: list[str]) -> list[dict]:
        raise U9NotImplemented("fetch_production_orders 未实现")

    # Phase 2：回写（本地 → U9），一期不调用
    def post_completion(self, order_no: str, device_code: str, qty: float) -> dict:
        raise U9NotImplemented("post_completion 为 Phase 2，待确认业务校验规则后实现")

    def post_material_issue(self, order_no: str, lines: list[dict]) -> dict:
        raise U9NotImplemented("post_material_issue 为 Phase 2，待确认业务校验规则后实现")


class ClassicU9Client(U9Client):
    """经典 U9（UBF WebService / SOAP）占位。"""

    def _todo(self, name: str) -> U9NotImplemented:
        return U9NotImplemented(
            f"{name}：经典 U9 SOAP 调用待实施商提供 WSDL 与方法定义后实现"
            f"（当前 U9_WSDL_URL={'已配置' if self.cfg.wsdl_url else '未配置'}）")

    def health_check(self) -> dict:
        raise self._todo("health_check")

    def fetch_items(self, codes=None, **_):
        raise self._todo("fetch_items")

    def fetch_bom(self, product_code, **_):
        raise self._todo("fetch_bom")

    def fetch_inventory(self, item_codes=None, wh=None, **_):
        raise self._todo("fetch_inventory")

    def fetch_production_orders(self, doc_nos=None, **_):
        raise self._todo("fetch_production_orders")


class U9CClient(U9Client):
    """U9 Cloud REST 客户端（只读查询）。"""

    # ------------------------------------------------------------ 授权
    def _cache_key(self) -> tuple:
        c = self.cfg
        return (c.base_url, c.client_id, c.ent_code, c.org_code, c.user_code)

    def _login(self) -> str:
        c = self.cfg
        params = urllib.parse.urlencode({
            "clientid": c.client_id, "clientsecret": c.client_secret,
            "userCode": c.user_code, "entcode": c.ent_code, "orgcode": c.org_code,
        })
        url = f"{c.base_url}{c.auth_path}?{params}"
        status, data = self._send("GET", url, c.auth_path, None, with_token=None)
        body = self._parse(data, c.auth_path, status)
        code = body.get("ResCode")
        token = body.get("Data")
        if self._ok(body) and isinstance(token, str) and token.strip():
            return token.strip()
        try:
            icode = int(code)
        except (TypeError, ValueError):
            icode = None
        hint = AUTH_ERRORS.get(icode, "")
        msg = self._res_msg(body)
        raise U9AuthError(
            "U9 授权失败" + (f"（ResCode={code}）" if code is not None else "")
            + (f"：{hint}" if hint else "") + (f"。U9 返回：{msg}" if msg else ""),
            res_code=code, http_status=status)

    def _token(self, force: bool = False) -> str:
        key = self._cache_key()
        now = time.time()
        with _TOKEN_LOCK:
            hit = _TOKEN_CACHE.get(key)
            if hit and not force and hit[1] > now:
                return hit[0]
        token = self._login()
        with _TOKEN_LOCK:
            _TOKEN_CACHE[key] = (token, now + self.cfg.token_ttl_seconds)
        return token

    def _invalidate(self) -> None:
        with _TOKEN_LOCK:
            _TOKEN_CACHE.pop(self._cache_key(), None)

    # ------------------------------------------------------------ HTTP
    def _send(self, method: str, url: str, path: str, body: bytes | None,
              with_token: str | None) -> tuple[int, bytes]:
        headers = {"Accept": "application/json", "User-Agent": "material-flow-u9/1"}
        if body is not None:
            headers["Content-Type"] = self.cfg.content_type
        if with_token:
            headers["token"] = with_token
        t0 = time.monotonic()
        deadline = getattr(self, "deadline", None)
        if deadline is not None and t0 > deadline:
            raise U9DeadlineExceeded(
                f"U9 同步超过时限（U9_SYNC_MAX_SECONDS={getattr(self, 'deadline_seconds', '?')} 秒），已中止；"
                "请缩小范围（指定料号/单号/设备编码）后重试")
        try:
            status, data = self.transport(method, url, headers, body,
                                          float(self.cfg.timeout_seconds), self.cfg.verify_ssl)
        except (socket.timeout, TimeoutError) as e:
            raise U9Error(f"U9 请求超时（{self.cfg.timeout_seconds} 秒）：{path}") from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise U9Error(f"U9 请求超时（{self.cfg.timeout_seconds} 秒）：{path}") from e
            raise U9Error(f"无法连接 U9 服务器（{reason}）：请检查 U9_BASE_URL 与网络连通") from e
        except OSError as e:
            raise U9Error(f"无法连接 U9 服务器（{e}）：请检查 U9_BASE_URL 与网络连通") from e
        log.info("u9 %s %s -> %s (%d ms)", method, path, status, int((time.monotonic() - t0) * 1000))
        return status, data

    @staticmethod
    def _ok(body: dict) -> bool:
        code = body.get("ResCode")
        if code is None:
            return body.get("Success") is True
        try:
            return int(code) == 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _res_msg(body: dict) -> str:
        for k in ("ResMsg", "Message", "Exception", "msg"):
            v = body.get(k)
            if v:
                return str(v)[:500]
        return ""

    @staticmethod
    def _parse(data: bytes, path: str, status: int) -> dict:
        text = (data or b"").decode("utf-8-sig", errors="replace").strip()
        try:
            body = json.loads(text) if text else {}
        except ValueError:
            if status >= 400:
                raise U9Error(f"U9 接口 HTTP {status}：{path}（{text[:200]}）", http_status=status)
            raise U9Error(f"U9 返回的不是 JSON：{path}（{text[:200]}）", http_status=status)
        if isinstance(body, str):  # 个别网关把 JSON 再包一层字符串
            try:
                body = json.loads(body)
            except ValueError:
                body = {"Data": body, "ResCode": 0 if status < 400 else status}
        if not isinstance(body, dict):
            body = {"ResCode": 0, "Data": body}
        if status >= 400 and "ResCode" not in body:
            body["ResCode"] = status
        return body

    def _post(self, path: str, payload: Any) -> list:
        """POST 业务接口，返回 Data 列表；token 失效时重登重试一次。"""
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.cfg.base_url}{path}"
        for attempt in (1, 2):
            token = self._token(force=attempt == 2)
            status, data = self._send("POST", url, path, raw, with_token=token)
            body = self._parse(data, path, status)
            if self._ok(body):
                d = body.get("Data")
                if d is None:
                    return []
                return d if isinstance(d, list) else [d]
            code = body.get("ResCode")
            try:
                icode = int(code)
            except (TypeError, ValueError):
                icode = None
            if attempt == 1 and (status in (401, 403) or icode in TOKEN_INVALID_CODES):
                self._invalidate()
                continue
            msg = self._res_msg(body)
            hint = CLR_HINT if "clrlib" in msg.lower() or "程序集信任" in msg else ""
            if hint:
                msg = msg.split("有关此错误的详细信息")[0].strip()
            raise U9Error(f"U9 接口 {path} 返回错误（ResCode={code}）：{msg or '无错误信息'}{hint}",
                          res_code=code, http_status=status)
        raise U9Error(f"U9 接口 {path} 授权重试后仍失败")  # pragma: no cover

    def _post_batched(self, path: str, keys: list[str], build: Callable[[str], dict],
                      errors: list[dict] | None = None) -> list:
        """按 batch_size 分批查询。整批报业务错误时逐条重试，单条失败记入 errors 不中断。"""
        out: list = []

        def record(key: str, e: U9Error) -> None:
            if errors is not None:
                errors.append({"key": key, "error": str(e)})

        for chunk in _chunks(keys, self.cfg.batch_size):
            try:
                out.extend(self._post(path, [build(k) for k in chunk]))
                continue
            except U9AuthError:
                raise
            except U9Error as e:
                if e.res_code is None:  # 网络/HTTP 层错误：直接上抛，不逐条重试
                    raise
                if len(chunk) == 1:
                    record(chunk[0], e)
                    continue
            for k in chunk:  # 整批业务错误：逐条重试，定位是哪条的问题
                try:
                    out.extend(self._post(path, [build(k)]))
                except U9AuthError:
                    raise
                except U9Error as e:
                    if e.res_code is None:
                        raise
                    record(k, e)
        return out

    # ------------------------------------------------------------ 业务
    def health_check(self) -> dict:
        t0 = time.monotonic()
        self._invalidate()
        self._token(force=True)
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000),
                "base_url": self.cfg.base_url, "org_code": self.cfg.org_code,
                "message": "U9C 授权登录成功"}

    def fetch_items(self, codes: list[str], errors: list[dict] | None = None) -> list[dict]:
        codes = _dedupe(codes)
        if not codes:
            return []
        return self._post_batched(PATH_ITEMS, codes,
                                  lambda c: {"ItemMaster": {"Code": c}, "OtherID": c}, errors)

    def _query_boms(self, codes: list[str], errors: list[dict] | None = None) -> dict[str, dict]:
        org = self.cfg.org_code
        build = (lambda c: {"Org": {"Code": org}, "ItemMaster": {"Code": c}, "OtherID": c}) if org \
            else (lambda c: {"ItemMaster": {"Code": c}, "OtherID": c})
        raw = self._post_batched(PATH_BOM, codes, build, errors)
        by_code: dict[str, list] = {}
        for b in raw:
            if isinstance(b, dict):
                by_code.setdefault(mappers.bom_item_code(b), []).append(b)
        return {k: mappers.pick_bom(v) for k, v in by_code.items() if k}

    def fetch_bom(self, product_code: str, *, recursive: bool | None = None,
                  max_depth: int | None = None) -> dict:
        """取某母件的 BOM 并（可选）逐层展开。

        返回 {product_code, product_name, product_spec, product_unit, product_form, bom_version,
              found, truncated, lines:[{level, parent_code, material_code, material_name, spec,
              unit, qty, material_form, sequence}], errors:[...]}
        level 口径与人工导出的聚合 BOM 一致：母件（设备）本身 = 1，直接子件 = 2，依次递增。
        """
        cfg = self.cfg
        recursive = cfg.bom_recursive if recursive is None else recursive
        max_depth = cfg.bom_max_depth if max_depth is None else max_depth
        product_code = (product_code or "").strip()
        errors: list[dict] = []
        today = time.strftime("%Y-%m-%d")
        boms: dict[str, dict | None] = {}
        top = self._query_boms([product_code], errors)
        boms.update({product_code: top.get(product_code)})
        root = boms[product_code]
        result = {"product_code": product_code, "product_name": "", "product_spec": "",
                  "product_unit": "", "product_form": "", "bom_version": "",
                  "found": root is not None, "truncated": False, "lines": [], "errors": errors}
        if root is None:
            return result
        result["product_name"] = mappers.s(mappers.g(root, "m_itemMaster.m_name"))
        result["bom_version"] = mappers.s(mappers.g(root, "m_bOMVersionCode"))
        result["product_unit"] = mappers.s(mappers.first(root, "m_productUOM.m_name", "m_productUOM.m_code"))

        children: dict[str, list[dict]] = {product_code: mappers.bom_components(root, today)}
        info: dict[str, dict] = {}
        # 广度优先逐层批量查子件 BOM（一层一次批量请求），depth = 母件下第几层
        frontier = [c["material_code"] for c in children[product_code]]
        depth = 1
        enrich_done: set[str] = set()
        while recursive and frontier and depth < max_depth:
            todo = _dedupe(c for c in frontier if c not in boms)
            if cfg.bom_enrich and todo:
                self._enrich(todo, info, enrich_done)
                # 已知是采购件等不可展开形态的，不再查 BOM
                todo = [c for c in todo if not info.get(c, {}).get("item_form")
                        or info[c]["item_form"] in mappers.EXPANDABLE_FORMS]
            found = self._query_boms(todo, errors) if todo else {}
            nxt: list[str] = []
            for code in todo:
                b = found.get(code)
                boms[code] = b
                if b is not None:
                    children[code] = mappers.bom_components(b, today)
                    nxt.extend(c["material_code"] for c in children[code])
            frontier = nxt
            depth += 1

        # 深度优先展开成行（父项后紧跟其子项），带环路保护与行数上限
        lines: list[dict] = []

        def walk(code: str, level: int, path: tuple[str, ...]):
            for comp in children.get(code, []):
                if len(lines) >= cfg.bom_max_lines:
                    result["truncated"] = True
                    return
                cc = comp["material_code"]
                lines.append({"level": level, "parent_code": code, **comp})
                if cc in path or cc == code:
                    errors.append({"key": cc, "error": f"BOM 环路：{' → '.join(path + (cc,))}，已停止展开"})
                    continue
                if level - 1 < max_depth:
                    walk(cc, level + 1, path + (cc,))

        walk(product_code, 2, (product_code,))
        if recursive and frontier and depth >= max_depth:
            result["truncated"] = True

        if cfg.bom_enrich:
            self._enrich([product_code] + [ln["material_code"] for ln in lines], info, enrich_done)
        p = info.get(product_code, {})
        result["product_name"] = result["product_name"] or p.get("name", "")
        result["product_spec"] = p.get("specification", "")
        result["product_unit"] = result["product_unit"] or p.get("unit", "")
        result["product_form"] = p.get("item_form", "")
        for ln in lines:
            it = info.get(ln["material_code"], {})
            ln["material_name"] = ln.get("material_name") or it.get("name", "")
            ln["spec"] = it.get("specification", "")
            ln["unit"] = ln.get("unit") or it.get("unit", "")
            ln["material_form"] = it.get("item_form", "")
            ln.pop("is_phantom", None)
        result["lines"] = lines
        return result

    def _enrich(self, codes: list[str], info: dict, done: set) -> None:
        todo = [c for c in _dedupe(codes) if c not in done]
        if not todo:
            return
        done.update(todo)
        for raw in self.fetch_items(todo, errors=None):
            try:
                m = mappers.map_item(raw)
            except ValueError:
                continue
            info[m["code"]] = m

    def fetch_inventory(self, item_codes: list[str] | None = None, wh: str | None = None,
                        errors: list[dict] | None = None) -> list[dict]:
        """库存现存量/可用量。接口请求体是单个对象：
        给料号 → 逐个料号查询（并发 U9_CONCURRENCY）；不给料号 → 按组织（+存储地点）整体查询。
        注意：现场 U9 的整体查询会在服务器端报 SQL CLR 错误，sync 层默认按本地料号逐个查。
        U9_INVENTORY_VIA_SQL=1（默认）时改用 QueryInfoBySql 直读库存表，见 _fetch_inventory_sql。"""
        wh = (wh if wh is not None else self.cfg.default_wh) or ""
        if getattr(self.cfg, "inventory_via_sql", False):
            return self._fetch_inventory_sql(item_codes or [], wh, errors)
        base = {"Org": self.cfg.org_code}
        if wh:
            base["Wh"] = wh
        codes = _dedupe(item_codes or [])
        if not codes:
            return self._post(PATH_INVENTORY, base)
        self._token()  # 先登录一次，避免并发线程同时登录

        def one(code: str):
            try:
                return code, self._post(PATH_INVENTORY, {**base, "ItemCode": code}), None
            except U9AuthError:
                raise
            except U9Error as e:
                if e.res_code is None:
                    raise
                return code, [], e

        out: list = []
        workers = max(1, min(self.cfg.concurrency, len(codes)))
        if workers == 1:
            results = map(one, codes)
        else:
            from concurrent.futures import ThreadPoolExecutor
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                results = list(pool.map(one, codes))
            finally:
                pool.shutdown(wait=True)
        for code, rows, err in results:
            if err is not None:
                if errors is not None:
                    errors.append({"key": code, "error": str(err)})
                continue
            out.extend(rows)
        return out

    def _fetch_inventory_sql(self, item_codes: list[str], wh: str,
                             errors: list[dict] | None = None) -> list[dict]:
        """InvTrans_WhQoh 按 料号+存储地点+库位+批号 汇总，返回与 Invtrans 接口同形的 m_* 行。

        现存量 = 各存储类型 StoreQty 之和；可用量 ≈ 存储类型 4（可用）的 StoreQty − 预留占用 ResvOccupyStQty
        （U9 正式可用量还会扣待发等，这里是近似）。只发固定 SELECT，编码先过白名单。
        """
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询库存：{org!r}")
        if wh and not _SAFE_KEY.match(wh):
            raise U9Error(f"存储地点编码不合规：{wh!r}")
        codes = []
        for c in _dedupe(item_codes):
            if _code_ok(c):
                codes.append(c)
            elif errors is not None:
                errors.append({"key": c, "error": "料号含非法字符，跳过库存查询"})
        if item_codes and not codes:
            return []
        base = ("select q.ItemInfo_ItemCode as ItemCode, max(q.ItemInfo_ItemName) as ItemName,"
                " w.Code as WhCode, max(wt.Name) as WhName, q.BinInfo_Code as BinCode,"
                " max(q.BinInfo_Name) as BinName, q.LotInfo_LotCode as LotCode,"
                " sum(q.StoreQty) as StoreQty,"
                " sum(case when q.StorageType=4 then q.StoreQty-q.ResvOccupyStQty else 0 end) as CanUseQty"
                " from InvTrans_WhQoh q join CBO_Wh w on w.ID=q.Wh"
                " left join CBO_Wh_Trl wt on wt.ID=w.ID and wt.SysMLFlag='zh-CN'"
                " join Base_Organization o on o.ID=q.LogisticOrg"
                f" where o.Code='{org}' and q.StoreQty<>0")
        if wh:
            base += f" and w.Code='{wh}'"
        group = " group by q.ItemInfo_ItemCode, w.Code, q.BinInfo_Code, q.LotInfo_LotCode"
        sqls = ([base + " and q.ItemInfo_ItemCode in (" + ",".join(f"'{c}'" for c in chunk) + ")" + group
                 for chunk in _chunks(codes, 500)] if codes else [base + group])
        out: list[dict] = []
        for sql in sqls:
            for row in self._post(PATH_SQL, {"SqlString": sql}):
                if not isinstance(row, dict):
                    continue
                r = {str(k).lower(): v for k, v in row.items()}
                out.append({"m_itemCode": r.get("itemcode"), "m_itemName": r.get("itemname"),
                            "m_whCode": r.get("whcode"), "m_whName": r.get("whname"),
                            "m_binCode": r.get("bincode") or "", "m_binName": r.get("binname") or "",
                            "m_lotCode": r.get("lotcode") or "", "m_storeQty": r.get("storeqty"),
                            "m_canUseQty": r.get("canuseqty")})
        return out

    def fetch_categories(self) -> tuple[list[dict], dict[str, str]]:
        """物料主分类全集 + 本组织料号→主分类编码（QueryInfoBySql，固定 SELECT）。"""
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询分类：{org!r}")
        cats = self._post(PATH_SQL, {"SqlString":
            "select c.Code as Code, max(t.Name) as Name from CBO_Category c"
            " left join CBO_Category_Trl t on t.ID=c.ID and t.SysMLFlag='zh-CN'"
            f" join Base_Organization o on o.ID=c.Org where o.Code='{org}' group by c.Code"})
        items = self._post(PATH_SQL, {"SqlString":
            "select i.Code as ItemCode, c.Code as CatCode from CBO_ItemMaster i"
            " join CBO_Category c on c.ID=i.MainItemCategory"
            f" join Base_Organization o on o.ID=i.Org where o.Code='{org}'"})
        low = lambda r: {str(k).lower(): v for k, v in r.items()} if isinstance(r, dict) else {}
        cat_rows = [{"code": low(r).get("code"), "name": low(r).get("name")} for r in cats]
        item_map = {}
        for r in items:
            d = low(r)
            if d.get("itemcode") and d.get("catcode"):
                item_map[str(d["itemcode"]).strip()] = str(d["catcode"]).strip()
        return cat_rows, item_map

    def fetch_production_orders(self, doc_nos: list[str],
                                errors: list[dict] | None = None) -> list[dict]:
        doc_nos = _dedupe(doc_nos)
        if not doc_nos:
            return []
        return self._post_batched(PATH_MO, doc_nos, lambda d: {"DocNo": d}, errors)

    def fetch_mo_states(self, doc_nos: list[str], errors: list[dict] | None = None) -> dict[str, dict]:
        """读 MO_MO 的单据状态与完工数量：{单号: {doc_state, product_qty, complete_qty}}。

        只发固定的只读 SELECT，单号/组织编码先过白名单；不合规的单号跳过并记入 errors。
        """
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询订单状态：{org!r}")
        keys = []
        for d in _dedupe(doc_nos):
            if _code_ok(d):
                keys.append(d)
            elif errors is not None:
                errors.append({"key": d, "error": "单号含非法字符，跳过状态查询"})
        out: dict[str, dict] = {}
        for chunk in _chunks(keys, 200):
            in_list = ",".join(f"'{k}'" for k in chunk)
            sql = ("select m.DocNo, m.DocState, m.ProductQty, m.TotalCompleteQty from MO_MO m"
                   " join Base_Organization o on o.ID=m.Org"
                   f" where o.Code='{org}' and m.DocNo in ({in_list})")
            for row in self._post(PATH_SQL, {"SqlString": sql}):
                if not isinstance(row, dict):
                    continue
                r = {str(k).lower(): v for k, v in row.items()}
                no = str(r.get("docno") or "").strip()
                if no:
                    out[no] = {"doc_state": r.get("docstate"), "product_qty": r.get("productqty"),
                               "complete_qty": r.get("totalcompleteqty")}
        return out

    def fetch_project_mos(self, projects: list[str], errors: list[dict] | None = None) -> list[dict]:
        """按项目号（精确匹配）列出挂在项目下的全部生产订单（MO）。

        业务口径（用户 2026-10-17 确认）：U9 项目号（如 JSBY-26B-001）= 本地订单，
        项目下每张 MO = 一台机台。只发固定只读 SELECT，项目号/组织编码先过白名单。
        返回小写键的行：docno, docstate, qty, itemcode, itemname, projectcode, projectname。
        """
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询项目订单：{org!r}")
        keys = []
        for p in _dedupe(projects):
            if _SAFE_KEY.match(p):
                keys.append(p)
            elif errors is not None:
                errors.append({"key": p, "error": "项目号含非法字符，跳过"})
        out: list[dict] = []
        seen: set[str] = set()
        for chunk in _chunks(keys, 100):
            in_list = ",".join(f"'{k}'" for k in chunk)
            sql = ("select m.DocNo as DocNo, m.DocState as DocState, m.ProductQty as Qty,"
                   " i.Code as ItemCode, i.Name as ItemName, p.Code as ProjectCode, t.Name as ProjectName"
                   " from MO_MO m join Base_Organization o on o.ID=m.Org"
                   " join CBO_Project p on p.ID=m.Project"
                   " left join CBO_Project_Trl t on t.ID=p.ID and t.SysMLFlag='zh-CN'"
                   " left join CBO_ItemMaster i on i.ID=m.ItemMaster"
                   f" where o.Code='{org}' and p.Code in ({in_list}) order by p.Code, m.DocNo")
            for row in self._post(PATH_SQL, {"SqlString": sql}):
                if not isinstance(row, dict):
                    continue
                r = {str(k).lower(): v for k, v in row.items()}
                no = str(r.get("docno") or "").strip()
                if no and no not in seen:
                    seen.add(no)
                    r["docno"] = no
                    r["projectcode"] = str(r.get("projectcode") or "").strip()
                    out.append(r)
        return out

    def fetch_recent_order_nos(self, limit: int = 2000,
                               errors: list[dict] | None = None,
                               project: str = "") -> list[str]:
        """取最近 N 个生产订单号（按 MO_MO.ID 倒序）：只发固定的只读 SELECT。

        组织编码先过白名单；返回去重后的单号列表（新→旧）。供按年份查找订单时
        先圈定候选单号，再逐单读 MODoc 按开工年过滤。
        project 可选：只取挂在该 U9 项目号下的订单（MO_MO.Project → CBO_Project.Code，
        前缀匹配，如 JSBY-26B-001 或 JSBY-26B）。U9 生产订单单号统一是 MO- 开头，
        JSBY-26B-xxx 是项目号而不是单号（2026-10-17 实测）。项目号过白名单。
        """
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询订单：{org!r}")
        prefix = (project or "").strip()
        if prefix and not _SAFE_KEY.match(prefix):
            raise U9Error(f"项目号不合规：{prefix!r}")
        try:
            n = max(1, min(int(limit), 5000))
        except (TypeError, ValueError):
            n = 2000
        sql = (f"select top {n} m.DocNo as DocNo from MO_MO m"
               f" join Base_Organization o on o.ID=m.Org")
        if prefix:
            # 项目号已过白名单（字母数字下划线中划线点），直接拼 LIKE 前缀匹配
            sql += (f" join CBO_Project p on p.ID=m.Project"
                    f" where o.Code='{org}' and p.Code like '{prefix}%'")
        else:
            sql += f" where o.Code='{org}'"
        sql += " order by m.ID desc"
        out: list[str] = []
        for row in self._post(PATH_SQL, {"SqlString": sql}):
            if not isinstance(row, dict):
                continue
            r = {str(k).lower(): v for k, v in row.items()}
            no = str(r.get("docno") or "").strip()
            if no and no not in out:
                out.append(no)
        return out

    def lookup_orders_sql(self, year: int | None = None, project: str = "",
                          limit: int = 2000) -> list[dict]:
        """按开工/完工年份和/或项目号直接在 MO_MO 上筛订单（一条只读 SELECT）。

        2026-10-17 实测：逐单读 MODoc 约 0.1 秒/单，2000 单要 3 分多钟，页面会卡住；
        这里一次 SQL 带出单号、产品料号/名称、数量、开工/完工日、DocState、项目号，约 0.2 秒。
        返回小写键的行：docno, itemcode, itemname, qty, startdate, completedate, docstate, projectcode。
        """
        org = self.cfg.org_code
        if not _SAFE_KEY.match(org or ""):
            raise U9Error(f"组织编码不合规，无法查询订单：{org!r}")
        prefix = (project or "").strip()
        if prefix and not _SAFE_KEY.match(prefix):
            raise U9Error(f"项目号不合规：{prefix!r}")
        try:
            n = max(1, min(int(limit), 5000))
        except (TypeError, ValueError):
            n = 2000
        where = [f"o.Code='{org}'"]
        if year is not None:
            y = int(year)
            if not 2000 <= y <= 2100:
                raise U9Error(f"年份超出范围（2000-2100）：{y}")
            where.append(f"(year(m.StartDate)={y} or year(m.CompleteDate)={y})")
        if prefix:
            where.append(f"p.Code like '{prefix}%'")
        sql = (f"select top {n} m.DocNo as DocNo, i.Code as ItemCode, i.Name as ItemName,"
               f" m.ProductQty as Qty, m.StartDate as StartDate, m.CompleteDate as CompleteDate,"
               f" m.DocState as DocState, p.Code as ProjectCode"
               f" from MO_MO m join Base_Organization o on o.ID=m.Org"
               f" left join CBO_ItemMaster i on i.ID=m.ItemMaster"
               f" left join CBO_Project p on p.ID=m.Project"
               f" where {' and '.join(where)} order by m.ID desc")
        out: list[dict] = []
        seen: set[str] = set()
        for row in self._post(PATH_SQL, {"SqlString": sql}):
            if not isinstance(row, dict):
                continue
            r = {str(k).lower(): v for k, v in row.items()}
            no = str(r.get("docno") or "").strip()
            if no and no not in seen:
                seen.add(no)
                r["docno"] = no
                out.append(r)
        return out


# 兼容旧名
U9CloudClient = U9CClient


def get_client(cfg: U9Config | None = None, transport: Transport | None = None) -> U9Client:
    """按 U9_MODE 返回对应客户端。"""
    from .config import get_config
    cfg = cfg or get_config()
    if cfg.mode == "u9c":
        return U9CClient(cfg, transport)
    return ClassicU9Client(cfg, transport)
