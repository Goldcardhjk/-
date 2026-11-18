# -*- coding: utf-8 -*-
"""进步壹号 Agent —— 支付通道层（v0.62 引入 / v0.67 对齐官方 Vibe Pay 文档）

架构定位（见 架构设计.md 支付域）：
    订单域（db.gift_orders / 未来的会员充值等）
        ↓ 只面对统一接口
    PaymentChannel  ├─ MockChannel     模拟通道（默认）：本地即刻标记，不产生任何真实交易
                    └─ VibePayChannel  支付宝 AI 付 · Vibe Pay（网页/桌面应用收款）：
                        alipay.trade.page.pay 本地 RSA2 签名生成收银台跳转 URL
                        → 默认浏览器打开（PC 扫码 / 登录均可）
                        → 程序轮询 alipay.trade.query 确认到账后落库
                        （官方明确：支付结果以查询/异步通知为准，不以同步跳转为准；
                         桌面形态无公网通知，轮询查单是唯一到账依据。）
                        v0.68 补齐官方 checklist 能力面：refund / refund_query /
                        close / parse_notify（异步通知验签，排除 sign/sign_type）。
    每笔订单只有一个状态机：待付款 → （已付款 | 失败 / 关闭），通道只负责
    推动「待付款 → 已付款」这一步，不改动订单的其他字段。

配置（pay_config.json，与程序同级；商户身份全局，与登录账号无关）：
    {
      "channel_default": "mock",
      "vibe": {
        "app_id":  "支付宝开放平台应用 APPID",
        "gateway": "https://openapi.alipay.com/gateway.do",
        "private_key":       "应用私钥 PKCS#8 / PKCS#1 PEM",
        "alipay_public_key": "支付宝公钥 SPKI PEM（验签用）",
        "timeout_sec": 900, "poll_interval": 3,
        "verify_response_sign": false
      }
    }

安全注意：
    - 私钥只存在本机 pay_config.json，不写进 SQLite、不进备份库；
      请把该文件加入备份排除清单，迁移机器时单独安全转移。
    - 轮询查单是桌面应用确认到账的唯一手段（无公网回调），订单以
      alipay.trade.query 的 TRADE_SUCCESS/FINISHED 为准；用户关窗不回滚
      已生成的订单，可事后在「最近订单」里对同一笔继续查询或人工核对。
    - 本模块不保存、不传输任何支付人敏感信息（姓名/手机号）；
      收银台由支付宝渲染，敏感信息不出本机。

依赖：标准库 + segno（可选，UI 已不再依赖二维码渲染，保留作工具）。
"""

import base64
import hashlib
import json
import os
import random
import time
import urllib.parse
import urllib.request

import db

# ---------------------------------------------------------------- 配置
CONFIG_PATH = os.path.join(db.BASE, "pay_config.json")

DEFAULTS = {
    "channel_default": "mock",
    "vibe": {
        "app_id": "",
        "gateway": "https://openapi.alipay.com/gateway.do",
        "private_key": "",
        "alipay_public_key": "",
        "timeout_sec": 900,
        "poll_interval": 3,
        "verify_response_sign": False,
    },
}

CHANNEL_LABELS = {
    "mock": "模拟支付（演示）",
    "vibe": "Vibe Pay · 支付宝",
}


class PaymentError(Exception):
    """通道层错误：message 直接面向用户展示。"""


def load_pay_config():
    cfg = json.loads(json.dumps(DEFAULTS))          # 深拷贝默认值
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    for k, v in user.items():
        if k == "vibe" and isinstance(v, dict):
            cfg["vibe"].update(v)
        elif k != "vibe":
            cfg[k] = v
    return cfg


def save_pay_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


# ---------------------------------------------------------------- RSA2（SHA256 / PKCS#1 v1.5）
# 支付宝要求 RSA2（SHA256withRSA）。为保持「标准库 + PIL + segno」的轻依赖
# 承诺，这里用纯 Python 大整数模幂实现签名——RSA 签名即 pow(m, d, n)，
# 2048 位一次耗时几毫秒到几十毫秒，轮询场景完全够用。

_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


def _der_tlv(data, off):
    """读一个 DER TLV：返回 (tag, 内容, 下一个 offset)。"""
    tag = data[off]
    ln = data[off + 1]
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(data[off + 2:off + 2 + n], "big")
        off += n
    start = off + 2
    return tag, data[start:start + ln], start + ln


def _der_int(content, off):
    tag, val, nxt = _der_tlv(content, off)
    if tag != 0x02:
        raise ValueError("期望 INTEGER，得到 0x%02x" % tag)
    return int.from_bytes(val, "big"), nxt


def _b64_body(pem):
    """接受两种形态：PEM（含 BEGIN/END 行）或官方密钥工具导出的裸 Base64 体。"""
    lines = [l.strip() for l in pem.strip().splitlines()]
    body = "".join(l for l in lines
                   if l and not l.startswith("-----"))
    try:
        return base64.b64decode(body)
    except Exception:
        raise ValueError("密钥内容不是合法的 Base64")


def parse_private_key(pem):
    """解析应用私钥（PKCS#8 或 PKCS#1），返回 {'n':…, 'd':…}。

    接受 PEM（含 BEGIN/END 行）或官方密钥工具导出的**裸 Base64 串**
    （'应用私钥RSA2048-敏感数据，请妥善保管.txt' 里的格式）。"""
    if not pem or not pem.strip():
        raise ValueError("私钥为空")
    der = _b64_body(pem)
    tag, seq, _ = _der_tlv(der, 0)
    if tag != 0x30:
        raise ValueError("私钥顶层应是 SEQUENCE")
    # 版本号 INTEGER 顺带吃掉位置；判别看下一个 TLV 的标签：
    # PKCS#8 外壳：SEQ{ INTEGER 0, AlgID SEQ(0x30), OCTET STRING(pkcs#1) }
    # PKCS#1 裸壳：SEQ{ version, 模数 INTEGER(0x02), e, d, p, q, … }
    head, off = _der_int(seq, 0)
    if off < len(seq) and seq[off] == 0x30:
        _tag_alg, _alg, off = _der_tlv(seq, off)     # 跳过算法标识 SEQ
        t3, octet, _ = _der_tlv(seq, off)            # 私钥 OCTET STRING
        if t3 != 0x04:
            raise ValueError("PKCS#8 缺少私钥 OCTET STRING")
        tag, seq, _ = _der_tlv(octet, 0)             # 内层是 PKCS#1 SEQUENCE
        _ver, off = _der_int(seq, 0)                 # 内层版本号
    # PKCS#1：off 已停在模数 INTEGER 上，直接落下面的公共尾部
    n_val, off = _der_int(seq, off)                  # 模数 n
    _e, off = _der_int(seq, off)                      # 公钥指数（签名不用）
    d_val, _ = _der_int(seq, off)                     # 私钥指数 d
    return {"n": n_val, "d": d_val}


def parse_public_key(pem):
    """解析支付宝公钥（SPKI：SEQ{ AlgID SEQ, BIT STRING{ SEQ{ n, e } } }）。

    接受 PEM（含 BEGIN/END 行）或平台页面/密钥工具复制的**裸 Base64 串**。"""
    if not pem or not pem.strip():
        raise ValueError("公钥为空")
    der = _b64_body(pem)
    tag, seq, _ = _der_tlv(der, 0)
    if tag != 0x30:
        raise ValueError("公钥顶层应是 SEQUENCE")
    t_alg, _alg, off = _der_tlv(seq, 0)              # 跳过算法标识 SEQ
    t_bit, bits, _ = _der_tlv(seq, off)              # 公钥 BIT STRING
    if t_bit != 0x03:
        raise ValueError("SPKI 缺少公钥 BIT STRING")
    inner = bits[1:]                                 # BIT STRING 首字节是未用位数
    _t, seq2, _ = _der_tlv(inner, 0)
    n_val, off2 = _der_int(seq2, 0)
    e_val, _ = _der_int(seq2, off2)
    return {"n": n_val, "e": e_val}


def _emsa_pkcs1_sha256(msg, k):
    h = hashlib.sha256(msg).digest()
    t = _DIGEST_INFO_SHA256 + h
    if k < len(t) + 11:
        raise ValueError("密钥太短")
    return b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def sign_sha256(msg: bytes, priv) -> str:
    k = (priv["n"].bit_length() + 7) // 8
    m = int.from_bytes(_emsa_pkcs1_sha256(msg, k), "big")
    s = pow(m, priv["d"], priv["n"])
    return base64.b64encode(s.to_bytes(k, "big")).decode()


def verify_sha256(msg: bytes, sig_b64: str, pub) -> bool:
    try:
        k = (pub["n"].bit_length() + 7) // 8
        s = int.from_bytes(base64.b64decode(sig_b64), "big")
        m = pow(s, pub["e"], pub["n"])
        return m.to_bytes(k, "big") == _emsa_pkcs1_sha256(msg, k)
    except Exception:
        return False


# ---------------------------------------------------------------- 生成随机素数（仅测试用途）
def _is_probable_prime(n, rounds=28):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def generate_keypair(bits=2048):
    """测试专用：纯 Python 生成 RSA 密钥对（当面付线上使用请用
    支付宝密钥工具生成，不要用测试代码的随机源）。"""
    while True:
        p = random.getrandbits(bits // 2) | (1 << (bits // 2 - 1)) | 1
        if _is_probable_prime(p):
            break
    while True:
        q = random.getrandbits(bits // 2) | (1 << (bits // 2 - 1)) | 1
        if q != p and _is_probable_prime(q):
            break
    n = p * q
    e = 65537
    phi = (p - 1) * (q - 1)
    d = pow(e, -1, phi)
    return ({"n": n, "e": e}, {"n": n, "d": d})


def _int_der(v):
    b = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + _len_der(len(b)) + b


def _len_der(ln):
    if ln < 0x80:
        return bytes([ln])
    b = ln.to_bytes((ln.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _seq_der(*parts):
    body = b"".join(parts)
    return b"\x30" + _len_der(len(body)) + body


def private_key_to_pkcs1_pem(priv, pub_e=65537):
    """测试专用：把密钥对序列化成 PKCS#1 PEM。"""
    n, d = priv["n"], priv["d"]
    der = _seq_der(_int_der(0), _int_der(n), _int_der(pub_e), _int_der(d))
    b64 = base64.b64encode(der).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN RSA PRIVATE KEY-----\n%s\n-----END RSA PRIVATE KEY-----" % "\n".join(lines)


def public_key_to_spki_pem(pub):
    """测试专用：SPKI 公钥 PEM。"""
    der = _seq_der(
        _seq_der(bytes.fromhex("06092a864886f70d010101"), b"\x05\x00"),
        b"\x03" + _len_der(len(_seq_der(_int_der(pub["n"]), _int_der(pub["e"]))) + 1)
        + b"\x00" + _seq_der(_int_der(pub["n"]), _int_der(pub["e"])))
    b64 = base64.b64encode(der).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----" % "\n".join(lines)


# ---------------------------------------------------------------- 支付宝网关调用
def _canonical(params):
    """待签名串：按 key 字典序拼 k=v（不含 sign，值为原始文本）。

    ⚠️ 2026-11-11 网关实证修正：**sign_type 参与待签名串**。官方文档
    「自行拼接」小节写的"不含 sign/sign_type"是**异步通知验签**的规则；
    请求签名以网关 debug 返回的验签串为准（实测字段序：
    app_id<biz_content<charset<format<method<sign_type<timestamp<version）。
    """
    return "&".join("%s=%s" % (k, params[k]) for k in sorted(params))


def _canonical_notify(payload):
    """异步通知验签串：按 key 字典序拼 k=v，**排除 sign 与 sign_type**。

    官方「自行拼接」小节规定通知验签不含这两个字段，与请求签名（sign_type
    参与）是两套规则，不能复用 _canonical。
    """
    return "&".join("%s=%s" % (k, payload[k]) for k in sorted(payload)
                    if k not in ("sign", "sign_type") and payload[k] != "")


def _biz_json(biz):
    """biz_content 的 JSON 序列化：**必须紧凑**（分隔符不带空格）。

    2026-11-15 网关实证：支付宝按紧凑 JSON 重算待签名串，若我们传
    `{"a": "1"}`（冒号后有空格），签名串就多出空格 → invalid-signature。
    """
    return json.dumps(biz, ensure_ascii=False, separators=(",", ":"))


def _call_gateway(cfg, method, biz_content):
    """按开放平台规范签名并请求，返回 (ok, 解析后的 JSON dict 或错误文案)。"""
    import datetime as _dt
    base = {
        "app_id": cfg.get("app_id", ""),
        "method": method,
        "format": "JSON",
        "charset": "utf-8",
        "timestamp": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "biz_content": _biz_json(biz_content),
    }
    try:
        priv = parse_private_key(cfg.get("private_key", ""))
        # 网关实证（2026-11-11）：sign_type 先置于参数集再签名
        base["sign_type"] = "RSA2"
        base["sign"] = sign_sha256(_canonical(base).encode("utf-8"), priv)
    except ValueError as ex:
        return False, "私钥不可用：%s" % ex
    form = {k: v for k, v in base.items()}
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(cfg.get("gateway", ""), data=data,
                                 headers={"Content-Type":
                                          "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "replace")
    try:
        return True, json.loads(text)
    except ValueError:
        return False, "网关返回非 JSON：%s" % text[:200]


# ---------------------------------------------------------------- 通道
class MockChannel:
    """模拟通道：只做本地标记（沿用 db.mock_pay 语义），不产生真实交易。"""
    name = "mock"

    @staticmethod
    def pay(oid):
        db.mock_pay(oid)
        return "模拟已付款"


class VibePayChannel:
    """Vibe Pay（支付宝官方 AI 应用收款 · 网页/桌面应用）。

    按官方《网页应用收款接入》文档实现（2026-11 校准）：
      1. alipay.trade.page.pay「统一收单下单并支付页面」——本地 RSA2 签名
         生成收银台跳转 URL，默认浏览器打开（PC 扫码 / 登录支付均可）；
         桌面应用无公网回执，支付结果以【查询接口】为准（官方明确要求）。
      2. alipay.trade.query 轮询确认到账（WAIT_BUYER_PAY / TRADE_SUCCESS / …）。

    旧版 precreate（当面付）已退役：官方 Vibe Pay 网页/桌面形态走 page.pay。
    """
    name = "vibe"

    def __init__(self, cfg=None):
        if cfg is None:
            cfg = load_pay_config()["vibe"]
        self.cfg = cfg

    def ready(self):
        """配置齐套且私钥/支付宝公钥可解析时返回 (True, '')，否则 (False, 原因)。"""
        cfg = self.cfg
        if not cfg.get("app_id"):
            return False, "未填 APPID"
        if not cfg.get("gateway"):
            return False, "未填网关地址"
        try:
            parse_private_key(cfg.get("private_key", ""))
        except ValueError as ex:
            return False, "应用私钥：%s" % ex
        try:
            parse_public_key(cfg.get("alipay_public_key", ""))
        except ValueError as ex:
            return False, "支付宝公钥：%s" % ex
        return True, ""

    @staticmethod
    def make_out_trade_no(oid):
        """商户订单号：唯一、可追溯到本机订单（时间戳防同号）。"""
        return "JBYH%d%06d" % (oid, int(time.time() * 100) % 1000000)

    def start_payment(self, out_trade_no, amount, subject, return_url=""):
        """发起收款：本地签名生成收银台跳转 URL（不调网关、零网络）。

        返回 {'pay_url': 可用默认浏览器打开的完整收银台地址,
              'out_trade_no': 商户订单号}。
        官方注意点：subject 不带特殊字符 /，= &；金额两位小数；
        product_code 固定 FAST_INSTANT_TRADE_PAY（电脑网站支付）。
        """
        import datetime as _dt
        cfg = self.cfg
        biz = {
            "out_trade_no": out_trade_no,
            "total_amount": ("%.2f" % float(amount)),
            "subject": str(subject).replace("/", " ").replace("=", " ")
                        .replace("&", " ")[:128],
            "product_code": "FAST_INSTANT_TRADE_PAY",
        }
        if return_url:
            biz["return_url"] = return_url
        base = {
            "app_id": cfg.get("app_id", ""),
            "method": "alipay.trade.page.pay",
            "format": "JSON",
            "charset": "utf-8",
            "timestamp": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "notify_url": cfg.get("notify_url", ""),
            "return_url": return_url or cfg.get("return_url", ""),
            "biz_content": _biz_json(biz),
        }
        base = {k: v for k, v in base.items() if v != ""}
        try:
            priv = parse_private_key(cfg.get("private_key", ""))
        except ValueError as ex:
            raise PaymentError("私钥不可用：%s" % ex)
        # 网关实证（2026-11-11）：sign_type 参与待签名串（先置再签）
        base["sign_type"] = "RSA2"
        base["sign"] = sign_sha256(_canonical(base).encode("utf-8"), priv)
        gateway = cfg.get("gateway", "")
        query = urllib.parse.urlencode(base)          # UTF-8 百分号编码
        return {"pay_url": gateway + "?" + query,
                "out_trade_no": out_trade_no}

    def query(self, out_trade_no):
        """轮询查单：返回 (状态, 提示, 支付宝交易号)。

        状态 ∈ {"WAIT", "SUCCESS", "FAIL"}：
          WAIT   收银台已开但买家未付款 / 交易平台尚无此单；
          SUCCESS TRADE_SUCCESS 或 TRADE_FINISHED；
          FAIL   明确失败（关单等）或网关异常。
        官方注意点：同步 return 不可靠，支付结果只以本接口（或异步通知
        +验签）为准——桌面形态无公网通知，轮询本接口是唯一依据。
        """
        ok, data = _call_gateway(self.cfg, "alipay.trade.query", {
            "out_trade_no": out_trade_no,
        })
        if not ok:
            return "FAIL", str(data), ""
        resp = (data or {}).get("alipay_trade_query_response", {})
        if resp.get("code") == "10000":
            trade_status = resp.get("trade_status", "")
            if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
                return "SUCCESS", trade_status, resp.get("trade_no", "")
            if trade_status in ("WAIT_BUYER_PAY", ""):
                return "WAIT", trade_status or "未付款", ""
            return "FAIL", trade_status, ""
        sub_code = resp.get("sub_code", "")
        # 收银台刚打开、订单尚未在支付宝侧建立时查不到，属正常等待
        if sub_code in ("ACQ.TRADE_NOT_EXIST", "ACQ.TRADE_STATUS_ERROR"):
            return "WAIT", "等待买家付款", ""
        msg = resp.get("sub_msg") or resp.get("msg") or ("网关错误 %s" % sub_code)
        return "FAIL", msg, ""

    # ---- 官方 checklist 要求的能力面：退款 / 退款查询 / 关单 / 异步通知 ----
    def refund(self, out_trade_no, amount, out_request_no, reason=""):
        """退款：返回 (状态, 提示, 支付宝交易号)。

        状态 ∈ {"SUCCESS", "DUPLICATE", "FAIL"}：
          SUCCESS   本次资金实际退回（fund_change=Y）；
          DUPLICATE 该退款请求号此前已成功（fund_change=N），幂等不重复退；
          FAIL      网关拒绝或业务失败。
        out_request_no 是退款幂等键：同一订单多次（部分）退款必须各不相同。
        退款结果的业务落库由调用方处理，通道只回协议层结论。
        """
        biz = {"out_trade_no": out_trade_no,
               "refund_amount": "%.2f" % float(amount),
               "out_request_no": out_request_no}
        if reason:
            biz["refund_reason"] = str(reason)[:256]
        ok, data = _call_gateway(self.cfg, "alipay.trade.refund", biz)
        if not ok:
            return "FAIL", str(data), ""
        resp = (data or {}).get("alipay_trade_refund_response", {})
        if resp.get("code") == "10000":
            if resp.get("fund_change") == "Y":
                return "SUCCESS", resp.get("msg", "退款成功"), resp.get("trade_no", "")
            return ("DUPLICATE", "该退款请求号已处理过（未再次扣款）",
                    resp.get("trade_no", ""))
        return "FAIL", resp.get("sub_msg") or resp.get("msg") or "退款失败", ""

    def refund_query(self, out_trade_no, out_request_no):
        """退款查询：返回 (状态, 退款状态说明, 退款金额)。"""
        ok, data = _call_gateway(self.cfg, "alipay.trade.fastpay.refund.query", {
            "out_trade_no": out_trade_no, "out_request_no": out_request_no})
        if not ok:
            return "FAIL", str(data), ""
        resp = (data or {}).get(
            "alipay_trade_fastpay_refund_query_response", {})
        if resp.get("code") == "10000":
            if resp.get("refund_amount") and resp.get("refund_status") in (
                    "REFUND_SUCCESS", None):
                return "SUCCESS", resp.get("refund_status", ""), resp.get("refund_amount", "")
            return "WAIT", resp.get("refund_status") or "无退款记录", ""
        return "FAIL", resp.get("sub_msg") or resp.get("msg") or "退款查询失败", ""

    def close(self, out_trade_no):
        """关闭交易：未付款订单置关闭，防止买家继续付款。"""
        ok, data = _call_gateway(self.cfg, "alipay.trade.close", {
            "out_trade_no": out_trade_no})
        if not ok:
            return "FAIL", str(data)
        resp = (data or {}).get("alipay_trade_close_response", {})
        if resp.get("code") == "10000":
            return "SUCCESS", resp.get("msg", "已关闭")
        return "FAIL", resp.get("sub_msg") or resp.get("msg") or "关单失败"

    def parse_notify(self, payload):
        """异步通知：验签 + 关键字段校验，返回 (是否支付成功, 明细 dict)。

        桌面形态没有公网 notify_url，生产以轮询查单为准；官方 checklist 仍
        要求通知处理代码就位，这里给出可直接挂 HTTP 端点的纯函数：
          1. 用【通知规则】的待签名串验签（排除 sign/sign_type）；
          2. 校验 app_id 与本地商户一致；
          3. 只有 TRADE_SUCCESS / TRADE_FINISHED 才算成功；
          4. 金额、订单归属与幂等由调用方按 out_trade_no 复核后处理。
        """
        payload = dict(payload or {})
        sign = payload.get("sign", "")
        if not sign:
            return False, {"error": "通知缺少 sign"}
        try:
            pub = parse_public_key(self.cfg.get("alipay_public_key", ""))
        except ValueError as ex:
            return False, {"error": "支付宝公钥不可用：%s" % ex}
        if not verify_sha256(_canonical_notify(payload).encode("utf-8"), sign, pub):
            return False, {"error": "通知验签失败"}
        if payload.get("app_id") != self.cfg.get("app_id"):
            return False, {"error": "app_id 与本地商户不一致"}
        status = payload.get("trade_status", "")
        info = {"out_trade_no": payload.get("out_trade_no", ""),
                "trade_no": payload.get("trade_no", ""),
                "total_amount": payload.get("total_amount", ""),
                "trade_status": status,
                "seller_id": payload.get("seller_id", "")}
        if status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            info["error"] = "非成功状态：%s" % (status or "空")
            return False, info
        return True, info


CHANNELS = {"mock": MockChannel, "vibe": VibePayChannel}


def get_channel(name):
    cls = CHANNELS.get(name)
    if cls is None:
        raise PaymentError("未知付款通道：%s" % name)
    return cls()


# ---------------------------------------------------------------- 二维码
def make_qr(text, out_path, scale=10, border=2):
    """用 segno 把二维码串渲染成 PNG；segno 缺失时返回 False（UI 退化为文本链接）。"""
    try:
        import segno
    except ImportError:
        return False
    qr = segno.make(text, error="l")
    qr.save(out_path, scale=scale, border=border)
    return True
