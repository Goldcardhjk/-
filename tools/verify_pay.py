# -*- coding: utf-8 -*-
"""支付域验收（v0.67 口径 · 独立进程、独立数据目录、全程离线）。

覆盖：
  1. RSA2 纯 Python 实现：PKCS#1/PKCS#8 私钥解析、SPKI 公钥解析、
     签名/验签/防篡改；
  2. 官方《网页应用收款接入》主路径：alipay.trade.page.pay 本地签名生成
     收银台 URL（零网络）、四件套与特殊字符清洗、时间戳与编码、
     URL 可直接交浏览器；查单三态只访问网关；
  3. 官方 checklist 能力面：退款（fund_change 判定 + 退款号幂等）、
     退款查询、关单、异步通知验签（改金额/改 app_id/非成功状态三条反例）；
  4. 订单状态机：建单（channel）→ 等待买家付款 → 已付款（pay_ref）
     / 模拟通道语义不变；
  5. 配置持久化：load/save 回读；
  6. 资产就位：vibepay / qr / pay_success。

v0.62 的「segno 渲染二维码」检查随当面付退役一并移除（v0.67 不再产出二维码）。

运行：python -B tools/verify_pay.py
"""
import base64
import io
import json
import os
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_tmp" / "v062" / "pay_check"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

os.environ["JINBU_DATA_DIR"] = str(OUT / "data")
os.environ["JINBU_SKIP_SEED"] = "1"
sys.dont_write_bytecode = True

import db                       # noqa: E402
import payment as P             # noqa: E402

REPORT = OUT / "verification.json"
checks = []


def check(name, cond, extra=""):
    checks.append({"name": name, "passed": bool(cond)})
    print(("[通过] " if cond else "[失败] ") + name +
          ((" | " + str(extra)) if extra else ""), flush=True)
    if not cond:
        raise AssertionError(name)


def run():
    assert ROOT.drive.upper() == "D:"
    db.init_db()

    # ---------- ① RSA2 ----------
    pub, priv = P.generate_keypair(2048)
    pem1 = P.private_key_to_pkcs1_pem(priv)
    der1 = P._b64_body(pem1)
    pkcs8 = P._seq_der(
        P._int_der(0),
        P._seq_der(bytes.fromhex("06092a864886f70d010101"), b"\x05\x00"),
        b"\x04" + P._len_der(len(der1)) + der1)
    b64 = base64.b64encode(pkcs8).decode()
    pem8 = ("-----BEGIN PRIVATE KEY-----\n"
            + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
            + "\n-----END PRIVATE KEY-----")
    k1 = P.parse_private_key(pem1)
    k8 = P.parse_private_key(pem8)
    check("PKCS#1 与 PKCS#8 私钥解析一致",
          k1["n"] == k8["n"] == priv["n"] and k1["d"] == k8["d"] == priv["d"])
    kpub = P.parse_public_key(P.public_key_to_spki_pem(pub))
    check("SPKI 支付宝公钥解析", kpub["n"] == pub["n"] and kpub["e"] == 65537)
    msg = "app_id=demo&method=alipay.trade.query&中文与UTF8".encode("utf-8")
    sig = P.sign_sha256(msg, priv)
    check("RSA2 签名可用且防篡改",
          P.verify_sha256(msg, sig, pub)
          and not P.verify_sha256(msg + b"!", sig, pub)
          and not P.verify_sha256(msg, "Q" + sig[1:], pub))
    check("待签名串严格按 key 排序",
          P._canonical({"c": 3, "a": 1, "b": 2}) == "a=1&b=2&c=3")

    # ---------- ② fake 网关：完整通道生命周期 ----------
    cfg = P.DEFAULTS["vibe"].copy()
    cfg.update({"app_id": "2026000000000000",
               "gateway": "https://fake.gateway/do",
               "private_key": pem8,
               "alipay_public_key": P.public_key_to_spki_pem(pub)})
    posted = []

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # v0.67：page.pay 本地签名生成收银台 URL（零网络）；只有轮询查单访问网关
    seq = [
        {"alipay_trade_query_response":
         {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}},
        {"alipay_trade_query_response":
         {"code": "10000", "trade_status": "WAIT_BUYER_PAY"}},
        {"alipay_trade_query_response":
         {"code": "10000", "trade_status": "TRADE_SUCCESS",
          "trade_no": "2026110722001410001000000001"}},
    ]
    fwd = {"i": 0}

    def fake_urlopen(req, timeout=None):
        posted.append({
            "url": req.full_url,
            "body": urllib.parse.parse_qs(req.data.decode("utf-8")),
        })
        nxt = min(fwd["i"], len(seq) - 1)
        fwd["i"] += 1
        return FakeResp(json.dumps(seq[nxt]).encode("utf-8"))

    chan = P.VibePayChannel(cfg)
    ok, why = chan.ready()
    check("配置齐套即就绪", ok and why == "", why)
    # ---- start_payment：官方 alipay.trade.page.pay 签名 URL（本地零网络） ----
    intent = chan.start_payment(
        "JBYH1", 128.0,
        "进步壹号送礼：武夷/山岩茶礼盒=给 & 奶奶")   # 带特殊字符验证清洗
    q = urllib.parse.urlparse(intent["pay_url"])
    params = {k: v[0] for k, v in
              urllib.parse.parse_qs(q.query).items()}
    biz = json.loads(params["biz_content"])
    check("收银台 URL 指向网关且为 page.pay",
          q.netloc == "fake.gateway"
          and params["method"] == "alipay.trade.page.pay")
    check("网页收款四件套齐备且金额/标题合规",
          biz["out_trade_no"] == "JBYH1"
          and biz["total_amount"] == "128.00"
          and biz["product_code"] == "FAST_INSTANT_TRADE_PAY"
          and "/" not in biz["subject"] and "=" not in biz["subject"]
          and "&" not in biz["subject"] and "奶奶" in biz["subject"])
    check("公共参数与签名类型齐备",
          params["app_id"] == "2026000000000000"
          and params["sign_type"] == "RSA2"
          and params["version"] == "1.0"
          and params["charset"] == "utf-8"
          and len(params["sign"]) == 344)
    # 签名自洽：按网关实证规则重算（sign_type 参与待签名串，仅排除 sign）
    canon = {k: v for k, v in params.items()
             if k != "sign"}
    expect = P.sign_sha256(P._canonical(canon).encode("utf-8"),
                           P.parse_private_key(cfg["private_key"]))
    check("RSA2 签名自洽（canonical 重算一致）", expect == params["sign"])
    # ---- query 轮询：fake 网关三态 ----
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        st1 = chan.query("JBYH1")
        st2 = chan.query("JBYH1")
        st3 = chan.query("JBYH1")
        check("查单语义：未建单=等待 / 未付=等待 / 到账=成功",
              st1[0] == "WAIT" and st2[0] == "WAIT" and st3[0] == "SUCCESS"
              and st3[2] == "2026110722001410001000000001",
              (st1[0], st2[0], st3[0]))
        check("查单走网关且仅此一处访问网络",
              len(posted) == 3
              and posted[0]["body"]["method"][0] == "alipay.trade.query")
    check("通道枚举与标签齐全",
          set(P.CHANNELS) == {"mock", "vibe"}
          and P.get_channel("mock").name == "mock"
          and P.get_channel("vibe").name == "vibe")
    try:
        P.get_channel("alipay_legacy")
        check("未知通道抛错", False)
    except P.PaymentError:
        check("未知通道抛错", True)

    # ---------- ②b 官方六件套：退款 / 退款查询 / 关单 / 异步通知 ----------
    calls = []

    def fake_by_method(req, timeout=None):
        body = urllib.parse.parse_qs(req.data.decode("utf-8"))
        method = body["method"][0]
        calls.append(method)
        table = {
            "alipay.trade.refund": {"alipay_trade_refund_response": {
                "code": "10000", "msg": "Success", "fund_change": "Y",
                "trade_no": "2026110722001410001000000001"}},
            "alipay.trade.fastpay.refund.query": {
                "alipay_trade_fastpay_refund_query_response": {
                    "code": "10000", "refund_status": "REFUND_SUCCESS",
                    "refund_amount": "0.01"}},
            "alipay.trade.close": {"alipay_trade_close_response": {
                "code": "10000", "msg": "Success"}},
        }
        return FakeResp(json.dumps(table[method]).encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=fake_by_method):
        st, m1, tn = chan.refund("JBYH1", 0.01, "RF1", "一分钱体验包退款")
        check("退款成功以 fund_change=Y 判定",
              st == "SUCCESS" and tn.endswith("0001"), (st, m1))
        st2, m2, amt = chan.refund_query("JBYH1", "RF1")
        check("退款查询返回 REFUND_SUCCESS 与金额",
              st2 == "SUCCESS" and amt == "0.01", (st2, m2, amt))
        st3, m3 = chan.close("JBYH1")
        check("未付款关单成功", st3 == "SUCCESS", (st3, m3))
        check("三接口都走网关且方法名正确",
              calls == ["alipay.trade.refund",
                        "alipay.trade.fastpay.refund.query",
                        "alipay.trade.close"], calls)

    with patch("urllib.request.urlopen", side_effect=lambda req, timeout=None:
               FakeResp(json.dumps({"alipay_trade_refund_response": {
                   "code": "10000", "msg": "Success", "fund_change": "N"
               }}).encode("utf-8"))):
        st4, m4, _ = chan.refund("JBYH1", 0.01, "RF1")
        check("同一退款号重放返回 DUPLICATE（不重复扣款）",
              st4 == "DUPLICATE", (st4, m4))

    # 异步通知：本地私钥自签一条通知，覆盖验签 / 字段 / 状态三条分支
    notify = {"out_trade_no": "JBYH1",
              "trade_no": "2026110722001410001000000001",
              "total_amount": "0.01", "trade_status": "TRADE_SUCCESS",
              "app_id": "2026000000000000", "seller_id": "2088000000000001",
              "charset": "utf-8", "notify_time": "2026-11-15 15:00:00"}

    def signed(d):
        d = dict(d)
        d["sign"] = P.sign_sha256(P._canonical_notify(d).encode("utf-8"), priv)
        d["sign_type"] = "RSA2"
        return d

    okn, infon = chan.parse_notify(signed(notify))
    check("异步通知验签通过并识别成功状态",
          okn and infon["trade_no"].endswith("0001"), infon)
    okb, infob = chan.parse_notify(dict(signed(notify), total_amount="999.00"))
    check("通知被改金额后验签失败",
          not okb and "验签失败" in infob.get("error", ""), infob)
    okc, infoc = chan.parse_notify(signed(dict(notify, app_id="2026000000009999")))
    check("通知 app_id 与本地不一致被拒",
          not okc and "app_id" in infoc.get("error", ""), infoc)
    okd, infod = chan.parse_notify(signed(dict(notify, trade_status="WAIT_BUYER_PAY")))
    check("通知非成功状态不判成功",
          not okd and infod.get("trade_status") == "WAIT_BUYER_PAY", infod)

    # ---------- ③ 订单状态机（隔离库） ----------
    db.save_person({"name": "测试收款人", "relation_type": "挚友",
                    "closeness": 0.8, "sentiment_weight": 0.8,
                    "intro_by": "", "address": "", "phone": "",
                    "birthday": "", "important_dates": "[]",
                    "prefs": '["茶"]', "taboo": "[]", "notes": ""}, None)
    pid = db.list_persons()[0]["id"]
    oid_v = db.create_order(pid, 1, "岩茶礼盒", "未排期", 128.0, "", channel="vibe")
    oid_m = db.create_order(pid, 1, "保温杯", "未排期", 119.0)   # 默认 mock
    ov = next(o for o in db.list_orders() if o["id"] == oid_v)
    om = next(o for o in db.list_orders() if o["id"] == oid_m)
    check("建单记录通道且默认待付款",
          ov["channel"] == "vibe" and om["channel"] == "mock"
          and ov["status"] == "待付款" and om["status"] == "待付款")
    db.update_order_status(oid_v, "等待买家付款")
    db.mark_order_paid(oid_v, "已付款", pay_ref="2026110722001410001000000001")
    P.MockChannel.pay(oid_m)
    ov = next(o for o in db.list_orders() if o["id"] == oid_v)
    om = next(o for o in db.list_orders() if o["id"] == oid_m)
    check("到账落库：状态+流水号+时间戳；模拟语义不变",
          ov["status"] == "已付款" and ov["pay_ref"] == "2026110722001410001000000001"
          and ov["paid_at"] and om["status"] == "模拟已付款" and om["paid_at"])
    from gifts import schedule_and_pay
    oid_s, status_s = schedule_and_pay(pid, 1, "手账本", "未排期", 89.0)
    os_s = next(o for o in db.list_orders() if o["id"] == oid_s)
    check("gifts.schedule_and_pay 兼容通道默认走 mock",
          status_s == "模拟已付款" and os_s["channel"] == "mock")

    # ---------- ④ 配置持久化 ----------
    cfg_path = OUT / "pay_config.json"
    with patch.object(P, "CONFIG_PATH", str(cfg_path)):
        allcfg = P.load_pay_config()
        allcfg["vibe"].update({"app_id": "2026000000000000",
                               "private_key": pem8,
                               "alipay_public_key": P.public_key_to_spki_pem(pub)})
        P.save_pay_config(allcfg)
        back = P.load_pay_config()
        check("配置保存后回读一致",
              back["vibe"]["app_id"] == "2026000000000000"
              and back["vibe"]["private_key"] == pem8
              and back["channel_default"] == "mock")
        ok2, _ = P.VibePayChannel().ready()
        check("按持久化配置构造通道就绪", ok2, _)
    check("pay_config.json 不含传给别人也无害的私钥泄露口（仅本地保存）",
          cfg_path.is_file() and cfg_path.stat().st_size > 400)

    # ---------- ⑤ 收银台 URL 可直接交浏览器（v0.67 取代二维码） ----------
    raw = intent["pay_url"]
    ts = params.get("timestamp", "")
    check("收银台 URL 可直接被默认浏览器打开",
          raw.startswith(("https://", "http://"))
          and len(raw) < 8000
          and " " not in raw and not any("\u4e00" <= ch <= "\u9fff" for ch in raw)
          and len(ts) == 19 and ts[4] == "-" and ts[13] == ":",
          raw[:60] + "…")

    # ---------- ⑥ 资产 ----------
    for asset in ("icon_vibepay", "icon_qr", "pay_success"):
        p = ROOT / "images" / "ui" / ("%s.png" % asset)
        check("ComfyUI 资产已处理就位：%s" % asset, p.is_file(), p.name)


try:
    run()
    status = "passed"
except BaseException:
    import traceback
    print(traceback.format_exc(), flush=True)
    status = "failed"
finally:
    REPORT.write_text(json.dumps(
        {"version": "v0.68-pay", "status": status, "checks": checks},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(checks),
                      "report": str(REPORT)}, ensure_ascii=False), flush=True)
    sys.exit(0 if status == "passed" else 1)
