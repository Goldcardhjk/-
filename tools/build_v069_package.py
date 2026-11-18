# -*- coding: utf-8 -*-
"""生成并独立回读 v0.69 比赛试用 zip，不打包任何账号、密钥或数据库。"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "进步壹号_Payathon提交"
SOURCE = SUBMIT / "04_可试用作品_v069"
ZIP = SUBMIT / "进步壹号Agent_v0.69_可试用作品.zip"
REPORT = SUBMIT / "04_可试用作品_v069_校验.json"
EXPECTED = [
    "1_启动云端服务.bat", "2_启动桌面端.bat",
    "JinbuYihaoAgent_v069.exe", "JinbuYihaoCloudServer_v069.exe", "运行说明.txt",
]
FORBIDDEN = ("pay_config", "llm_config", "cloud_session", "cloud_client", ".env",
             "private_key", "accounts.json", ".db", ".sqlite", ".pem", ".key")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


files = []
for name in EXPECTED:
    path = SOURCE / name
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("缺少或空文件：" + str(path))
    files.append({"name": name, "bytes": path.stat().st_size, "sha256": sha256(path)})

for exe in ("JinbuYihaoAgent_v069.exe", "JinbuYihaoCloudServer_v069.exe"):
    if sha256(ROOT / "dist_v069" / exe) != sha256(SOURCE / exe):
        raise RuntimeError("试用目录与最新 dist 构建不一致：" + exe)

hits = [name for name in EXPECTED if any(word in name.lower() for word in FORBIDDEN)]
if hits:
    raise RuntimeError("敏感文件禁入检查失败：" + repr(hits))

tmp = ZIP.with_suffix(ZIP.suffix + ".tmp")
with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for name in EXPECTED:
        z.write(SOURCE / name, arcname=name)
os.replace(tmp, ZIP)

with zipfile.ZipFile(ZIP, "r") as z:
    names = z.namelist()
    bad_crc = z.testzip()
    archive_hits = [name for name in names if any(word in name.lower() for word in FORBIDDEN)]
    if bad_crc or set(names) != set(EXPECTED) or len(names) != 5 or archive_hits:
        raise RuntimeError("zip 回读失败：CRC=%r names=%r forbidden=%r" %
                           (bad_crc, names, archive_hits))

report = {
    "status": "passed", "generated_at": datetime.now().isoformat(timespec="seconds"),
    "zip": ZIP.name, "zip_bytes": ZIP.stat().st_size,
    "zip_sha256": sha256(ZIP), "file_count": len(EXPECTED),
    "files": files, "crc_ok": True, "sensitive_file_hits": [],
    "dist_matches_package": True, "real_model": False, "real_payment": False,
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

verification = ROOT / "_tmp" / "v069" / "package_check" / "verification.json"
if verification.exists():
    data = json.loads(verification.read_text(encoding="utf-8"))
    data.update({"zip_sha256": report["zip_sha256"], "zip_hash_matches": True,
                 "zip_file_count": 5, "sensitive_file_hits": 0})
    verification.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
