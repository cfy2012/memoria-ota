# -*- coding: utf-8 -*-
"""
@file  tools/msp-packer/msp_packer.py
@brief Memoria OS 打包器（图形界面）—— 应用包 / 音频包 / store 清单 + 审核上架
@usage 双击运行，或: D:\\py\\python.exe msp_packer.py    无界面自测: msp_packer.py --selftest

四步上架流程（审核制，2026-10-07 用户拍板）:
  1. 应用包页签打 .msp → 自动落桌面 + 自动生成审核邮件(.eml)并打开
  2. 邮件已预填收件人/校验码/附件 → 用户核对 CRC/SHA256 并试玩 → 通过则点发送
  3. 审核通过后用 store 清单页签把包加进 manifest.json 上架
  4. 商店按版本号判断更新（已装 < 包版本 → 可更新）

三页签:
  1. 应用包  —— BASIC 应用打 .msp（MBND 多文件节表，输出 名字-版本.msp 到桌面）
  2. 音频包  —— 单 payload MSPACK（对齐固件 package_format.cpp 50B 头）
  3. store 清单 —— manifest.json 生成（审核通过后的上架动作）

应用包 payload 规范（MBND 节表，GLM 定案 2026-10-07，固件侧按此解包）:
  b'MBND'                     4B  应用包标识
  u16  N                      条目数（小端，下同）
  N 条目录:  u16 name_len + name(utf-8) + u32 data_len
  数据区:    各条目数据按目录顺序紧凑排列
首条目固定为 manifest.json（name/ver/type/entry/icon），入口脚本 app.bas。

版本号规范: 三段 x.y.z → 头内 u32 = x*10000 + y*100 + z（1.2.0 → 10200），
固件/商店按 u32 数值比较判更新；包文件名 = 名字-版本.msp。
依赖: 仅 Python 3 标准库（tkinter/email），无需安装任何第三方包
"""
import json, struct, sys, zlib, os, time, hashlib
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formatdate

REVIEW_MAILBOX = "cfy_20120331@outlook.com"   # 应用上架审核邮箱（用户指定）

MAGIC = b"MSPACK"
HEADER_SIZE = 50
MAX_PAYLOAD = 1024 * 1024          # 固件 verify_package 上限 1MB
APP_TAG = b"MBND"
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz"
                 "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


# ---------------- 核心打包逻辑（与固件逐位对齐，可脱离 GUI 单测） ----------------

def crc32(data: bytes) -> int:
    """zlib 标准 CRC32 —— 与固件 crc32_compute 一致（初值/末异或 0xFFFFFFFF）"""
    return zlib.crc32(data) & 0xFFFFFFFF


def check_name(name: str):
    """固件白名单: [A-Za-z0-9_.-]，防路径穿越"""
    if not name or name in (".", ".."):
        raise ValueError("包名不合法: %r" % name)
    if any(c not in NAME_CHARS for c in name):
        raise ValueError("包名只允许 A-Za-z0-9_.- : %s" % name)


def ver_to_u32(text: str) -> int:
    """'1.2.0' → 10200；容错 1~3 段"""
    parts = (text.strip() or "0").split(".")
    if len(parts) > 3:
        raise ValueError("版本号最多三段 x.y.z: %s" % text)
    nums = []
    for p in parts:
        if not p.isdigit():
            raise ValueError("版本号段必须是数字: %s" % text)
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    if any(n > 99 for n in nums):
        raise ValueError("版本号每段 0~99: %s" % text)
    return nums[0] * 10000 + nums[1] * 100 + nums[2]


def pack_msp(name: str, version: int, payload: bytes) -> bytes:
    """打 MSPACK 包并回读自校验（模拟固件 verify_package 全部检查）"""
    check_name(name)
    if not 0 <= version <= 0xFFFFFFFF:
        raise ValueError("版本号超出 0~4294967295")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload %d 字节超过固件 1MB 上限" % len(payload))
    hdr = struct.pack("<6sI32sII", MAGIC, version, name.encode()[:31],
                      len(payload), crc32(payload))
    blob = hdr + payload
    # 回读自校验
    magic, ver, nm, psz, crc = struct.unpack("<6sI32sII", blob[:HEADER_SIZE])
    if magic != MAGIC or psz != len(payload) or crc != crc32(payload):
        raise RuntimeError("自校验失败（头部回读不一致）")
    if crc32(blob[HEADER_SIZE:HEADER_SIZE + psz]) != crc:
        raise RuntimeError("自校验失败（CRC 回算不一致）")
    return blob


def build_app_payload(manifest: dict, app_bas: bytes, extra_files=None) -> bytes:
    """MBND 应用包 payload：manifest.json + app.bas + 附加文件（节表见文件头规范）"""
    files = [("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                          indent=2).encode("utf-8")),
             ("app.bas", app_bas)]
    for fn, data in (extra_files or []):
        check_name(fn)
        files.append((fn, data))
    entries = b""
    body = b""
    for fn, data in files:
        nb = fn.encode("utf-8")
        if len(nb) > 65535:
            raise ValueError("文件名过长: %s" % fn)
        entries += struct.pack("<H", len(nb)) + nb + struct.pack("<I", len(data))
        body += data
    payload = APP_TAG + struct.pack("<H", len(files)) + entries + body
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("应用包 payload %d 字节超过固件 1MB 上限" % len(payload))
    return payload


def unpack_app_payload(payload: bytes):
    """回读 MBND 节表 → [(name, bytes)]；PC 端校验用"""
    if payload[:4] != APP_TAG:
        raise ValueError("不是 MBND 应用包")
    (n,) = struct.unpack_from("<H", payload, 4)
    off = 6
    dir_items = []
    for _ in range(n):
        (nl,) = struct.unpack_from("<H", payload, off); off += 2
        fn = payload[off:off + nl].decode("utf-8"); off += nl
        (dl,) = struct.unpack_from("<I", payload, off); off += 4
        dir_items.append((fn, dl))
    out = []
    for fn, dl in dir_items:
        out.append((fn, payload[off:off + dl])); off += dl
    return out


def pack_app(app_name: str, ver_text: str, entry_bas: bytes,
             extra_files=None, icon: str = "", color: str = "") -> bytes:
    """一键打应用包：manifest 组装 + MBND 节表 + MSPACK 头 + 全链自校验"""
    check_name(app_name)
    if not entry_bas:
        raise ValueError("app.bas 内容为空")
    ver_u = ver_to_u32(ver_text)
    manifest = {"name": app_name, "ver": ver_text, "ver_u32": ver_u,
                "type": "app", "entry": "app.bas",
                "built": time.strftime("%Y-%m-%d %H:%M")}
    if icon:
        manifest["icon"] = icon
    if color:
        manifest["color"] = color
    payload = build_app_payload(manifest, entry_bas, extra_files)
    blob = pack_msp(app_name, ver_u, payload)      # 头内 name=应用名, version=u32
    # 全链回读：头 → payload → MBND → manifest 一致性
    items = dict(unpack_app_payload(blob[HEADER_SIZE:]))
    if "manifest.json" not in items or "app.bas" not in items:
        raise RuntimeError("自校验失败：节表缺 manifest/app.bas")
    m2 = json.loads(items["manifest.json"].decode("utf-8"))
    if m2["name"] != app_name or m2["ver"] != ver_text:
        raise RuntimeError("自校验失败：manifest 名字/版本不一致")
    if items["app.bas"] != entry_bas:
        raise RuntimeError("自校验失败：app.bas 回读不一致")
    return blob


def desktop_dir():
    """Windows 桌面路径（含 OneDrive 重定向探测），失败回退脚本目录"""
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Desktop"),
                 os.path.join(home, "OneDrive", "Desktop"),
                 os.path.join(home, "OneDrive", "桌面"),
                 os.path.join(home, "桌面")):
        if os.path.isdir(cand):
            return cand
    return os.path.dirname(os.path.abspath(__file__))


def build_review_eml(msp_path: str, out_path: str = "") -> str:
    """生成审核邮件 .eml（收件人=审核邮箱，附件=.msp，正文=校验码三件套）。
    打包器调用后 os.startfile() 打开，用户核对通过点发送即完成审核提交。"""
    with open(msp_path, "rb") as f:
        blob = f.read()
    name = os.path.basename(msp_path)
    crc = "%08X" % (zlib.crc32(blob) & 0xFFFFFFFF)
    sha = hashlib.sha256(blob).hexdigest()
    msg = MIMEMultipart()
    msg["From"] = "Memoria OS 打包器 <packer@memoria.local>"
    msg["To"] = REVIEW_MAILBOX
    msg["Subject"] = Header("[Memoria 应用审核] %s" % name, "utf-8")
    msg["Date"] = formatdate(localtime=True)
    body = (
        "Memoria OS 应用包上架审核\n"
        "========================================\n"
        "包文件 : %s\n"
        "大小   : %d 字节\n"
        "CRC32  : %s\n"
        "SHA256 : %s\n"
        "========================================\n"
        "审核方法:\n"
        "  1. 用打包器重新打包同名应用，比对 CRC32/SHA256 一致\n"
        "  2. 或将 .msp 放入掌机 TF 卡实测运行\n"
        "检测通过 → 直接回复/转发本邮件即完成上架确认。\n"
        "（本邮件由 msp-packer 自动生成）\n" % (name, len(blob), crc, sha))
    msg.attach(MIMEText(body, "plain", "utf-8"))
    att = MIMEApplication(blob)
    att.add_header("Content-Disposition", "attachment",
                   filename=Header(name, "utf-8").encode())
    msg.attach(att)
    out = out_path or os.path.splitext(msp_path)[0] + "-审核.eml"
    with open(out, "wb") as f:
        f.write(msg.as_bytes())
    return out


def build_manifest(base_url: str, files: list) -> list:
    """生成 store 源清单（对齐 package_manager.cpp manifest 格式注释）"""
    entries = []
    for path in files:
        name = os.path.basename(path)
        check_name(name)
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > MAX_PAYLOAD:
            raise ValueError("%s 超过 1MB" % name)
        ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
        e = {"name": name, "version": time.strftime("%Y.%m.%d"),
             "size": len(data), "crc": crc32(data),
             "url": base_url.rstrip("/") + "/" + name}
        if ext in ("wav", "mp3", "m4a"):
            e["type"] = "media"        # media 包: 固件走 MSPACK 解包路径
        entries.append(e)
    return entries


def selftest() -> int:
    """无 GUI 自测：音频包 + 应用包全链回读"""
    ok = True

    # 1. 音频包
    payload = b"#!/memoria\nprint \"msp packer selftest\"\n" * 24
    blob = pack_msp("selftest", 1, payload)
    magic, ver, nm, psz, crc = struct.unpack("<6sI32sII", blob[:HEADER_SIZE])
    t = (magic == MAGIC and psz == len(payload) and crc == crc32(payload)
         and blob[HEADER_SIZE:] == payload)
    print(" audio-pack : %s" % ("PASS" if t else "FAIL")); ok &= t

    # 2. 版本号数值化
    t = (ver_to_u32("1.2.0") == 10200 and ver_to_u32("2.0") == 20000
         and ver_to_u32("3") == 30000)
    print(" ver->u32   : %s" % ("PASS" if t else "FAIL")); ok &= t

    # 3. 应用包全链
    bas = b'10 print "hello app"\n20 goto 10\n'
    blob = pack_app("demo-app", "1.2.0", bas,
                    extra_files=[("data.txt", b"abc")])
    magic, ver_u, nm, psz, crc = struct.unpack("<6sI32sII", blob[:HEADER_SIZE])
    items = dict(unpack_app_payload(blob[HEADER_SIZE:]))
    m2 = json.loads(items["manifest.json"].decode("utf-8"))
    t = (ver_u == 10200 and magic == MAGIC
         and set(items) == {"manifest.json", "app.bas", "data.txt"}
         and m2["ver"] == "1.2.0" and m2["ver_u32"] == 10200
         and m2["type"] == "app" and m2["entry"] == "app.bas"
         and items["app.bas"] == bas and items["data.txt"] == b"abc"
         and crc32(blob[HEADER_SIZE:]) == crc)
    print(" app-pack   : %s | %d B | ver_u32=%d | files=%s"
          % ("PASS" if t else "FAIL", len(blob), ver_u, sorted(items))); ok &= t

    # 4. manifest 空表
    m = build_manifest("https://example.com/pkg", [])
    t = (m == [])
    print(" manifest   : %s" % ("PASS" if t else "FAIL")); ok &= t

    # 5. 审核邮件 .eml（结构回读：收件人/附件/校验码）
    import email, tempfile
    from email import policy
    tmp_msp = os.path.join(tempfile.gettempdir(), "demo-app-1.2.0.msp")
    with open(tmp_msp, "wb") as f:
        f.write(blob)
    eml_path = build_review_eml(tmp_msp)
    with open(eml_path, "rb") as f:
        parsed = email.message_from_binary_file(f, policy=policy.default)
    atts = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    body = parsed.get_body(preferencelist=("plain",)).get_content() \
        if parsed.is_multipart() else parsed.get_content()
    t = (parsed["To"] == REVIEW_MAILBOX
         and "demo-app-1.2.0.msp" in atts
         and "CRC32" in body and "SHA256" in body)
    print(" review-eml : %s | to=%s | att=%s"
          % ("PASS" if t else "FAIL", parsed["To"], atts)); ok &= t
    os.remove(eml_path); os.remove(tmp_msp)

    print("selftest: %s" % ("ALL PASS" if ok else "存在 FAIL"))
    return 0 if ok else 1


# ---------------- 图形界面 ----------------

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    app = tk.Tk()
    app.title("Memoria OS 打包器 — 应用包 / 音频包 / store 清单")
    app.geometry("700x560")
    app.minsize(640, 500)

    style = ttk.Style(app)
    try:
        style.theme_use("vista")
    except Exception:
        pass
    style.configure("TButton", padding=(10, 4))
    style.configure("Acc.TButton", font=("Microsoft YaHei UI", 10, "bold"))

    log = scrolledtext.ScrolledText(app, height=10, state="disabled",
                                    font=("Consolas", 9), bg="#101418", fg="#d8e4f0")

    def say(msg, ok=True):
        log.configure(state="normal")
        log.tag_config("ok", foreground="#35c46a")
        log.tag_config("err", foreground="#ff6b6b")
        log.tag_config("dim", foreground="#8b949e")
        log.insert("end", msg + "\n", "ok" if ok else "err")
        log.see("end")
        log.configure(state="disabled")

    nb = ttk.Notebook(app)
    nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))

    # ---- 页 1: 应用包（BASIC 应用 → 名字-版本.msp 落桌面） ----
    f1 = ttk.Frame(nb, padding=14)
    nb.add(f1, text=" 应用包（BASIC → .msp） ")
    v_name = tk.StringVar()
    v_ver = tk.StringVar(value="1.0.0")
    v_bas = tk.StringVar()
    v_icon = tk.StringVar()
    v_color = tk.StringVar(value="#4fc1ff")
    extra = []
    lb1 = None   # 附加文件列表

    def r1(rr, label, var, w=46, btn=None):
        ttk.Label(f1, text=label).grid(row=rr, column=0, sticky="w", pady=5)
        e = ttk.Entry(f1, textvariable=var, width=w)
        e.grid(row=rr, column=1, columnspan=2, sticky="we", padx=6)
        if btn:
            ttk.Button(f1, text=btn[0], command=btn[1]).grid(row=rr, column=3)
        return e

    r1(0, "应用名（小写连字符）", v_name, 46,
       btn=None)
    r1(1, "版本号 x.y.z", v_ver, 46)
    r1(2, "入口脚本 app.bas", v_bas, 46,
       btn=("浏览…", lambda: (lambda p: p and (v_bas.set(p),
            v_name.set(v_name.get() or os.path.splitext(
                os.path.basename(p))[0].lower())))(filedialog.askopenfilename(
                    filetypes=[("BASIC 脚本", "*.bas"), ("全部", "*.*")]))))
    r1(3, "桌面图标字符（可选，如 ✦）", v_icon, 46)
    r1(4, "图标颜色（可选，#RRGGBB）", v_color, 46)

    ttk.Label(f1, text="附加文件（随包安装到应用目录）").grid(
        row=5, column=0, sticky="w", pady=(10, 2))
    lb1 = tk.Listbox(f1, height=4, font=("Consolas", 9))
    lb1.grid(row=6, column=0, columnspan=3, sticky="we", padx=(0, 6))

    def add_extra():
        for p in filedialog.askopenfilenames():
            if p not in extra:
                extra.append(p)
                lb1.insert("end", os.path.basename(p))

    def rm_extra():
        for i in reversed(lb1.curselection()):
            lb1.delete(i); extra.pop(i)

    bf = ttk.Frame(f1)
    bf.grid(row=6, column=3, sticky="n")
    ttk.Button(bf, text="添加", command=add_extra).pack(fill="x", pady=2)
    ttk.Button(bf, text="移除", command=rm_extra).pack(fill="x")

    def do_app():
        try:
            app_name = v_name.get().strip().lower().replace(" ", "-")
            ver = v_ver.get().strip()
            with open(v_bas.get(), "rb") as f:
                bas = f.read()
            ex = []
            for p in extra:
                with open(p, "rb") as f:
                    ex.append((os.path.basename(p), f.read()))
            blob = pack_app(app_name, ver, bas, ex,
                            icon=v_icon.get().strip(),
                            color=v_color.get().strip())
            out = os.path.join(desktop_dir(), "%s-%s.msp" % (app_name, ver))
            with open(out, "wb") as f:
                f.write(blob)
            say("app ok: %s" % out)
            say("  头 50 B + MBND payload %d B = %d B | ver_u32=%d | crc=0x%08X"
                % (len(blob) - HEADER_SIZE, len(blob), ver_to_u32(ver),
                   crc32(blob[HEADER_SIZE:])), ok=True)
            # 审核上架流程：生成审核邮件并打开，用户核对通过点发送
            try:
                eml = build_review_eml(out)
                say("  审核邮件: %s → %s" % (os.path.basename(eml),
                                              REVIEW_MAILBOX))
                os.startfile(eml)          # 默认邮件客户端打开，核对后点发送
                messagebox.showinfo(
                    "完成 · 待审核",
                    "应用包已放到桌面:\n%s\n\n"
                    "审核邮件已生成并打开（收件人 %s），\n"
                    "核对校验码并试玩通过后点发送，\n"
                    "再用「store 清单」页签上架。" % (out, REVIEW_MAILBOX))
            except Exception as me:
                say("  审核邮件生成失败（包本身已完成）: %s" % me, ok=False)
                messagebox.showinfo("完成", "应用包已放到桌面:\n%s" % out)
        except Exception as ex:
            say("app 失败: %s" % ex, ok=False)
            messagebox.showerror("错误", str(ex))

    ttk.Button(f1, text="打 包 到 桌 面", style="Acc.TButton",
               command=do_app).grid(row=7, column=0, columnspan=4,
                                    sticky="we", pady=12, ipady=2)
    ttk.Label(f1, text="输出: 桌面\\名字-版本.msp ｜ 商店按版本号判断更新"
              "（已装 < 包版本 → 可更新）｜ BASIC 语法不动，只加应用外壳",
              foreground="#888").grid(row=8, column=0, columnspan=4, sticky="w")
    f1.columnconfigure(1, weight=1)

    # ---- 页 2: 音频包 ----
    f2 = ttk.Frame(nb, padding=14)
    nb.add(f2, text=" 音频包（media .msp） ")
    v_name2 = tk.StringVar()
    v_ver2 = tk.StringVar(value="1")
    v_in = tk.StringVar()
    v_out = tk.StringVar()

    def mkrow(fr, rr, label, var, browse=False, save=False):
        ttk.Label(fr, text=label).grid(row=rr, column=0, sticky="w", pady=6)
        e = ttk.Entry(fr, textvariable=var, width=46)
        e.grid(row=rr, column=1, sticky="we", padx=6)
        if browse:
            def pick():
                p = filedialog.asksaveasfilename() if save else filedialog.askopenfilename()
                if p:
                    var.set(p)
            ttk.Button(fr, text="浏览…", command=pick).grid(row=rr, column=2)

    mkrow(f2, 0, "包名", v_name2)
    mkrow(f2, 1, "版本号（整数）", v_ver2)
    mkrow(f2, 2, "payload 文件", v_in, browse=True)
    mkrow(f2, 3, "输出 .msp", v_out, browse=True, save=True)
    ttk.Label(f2, text="media 包: 固件 MSPACK 解包路径（.wav/.mp3/.m4a → /audio/）",
              foreground="#888").grid(row=4, column=0, columnspan=3, sticky="w")

    def do_msp():
        try:
            name = v_name2.get().strip() or os.path.basename(v_in.get())
            name = os.path.splitext(name)[0]
            ver = int(v_ver2.get())
            with open(v_in.get(), "rb") as f:
                payload = f.read()
            blob = pack_msp(name, ver, payload)
            out = v_out.get().strip() or name + ".msp"
            with open(out, "wb") as f:
                f.write(blob)
            say("msp ok: %s | 头 50 B + payload %d B = %d B | crc=0x%08X"
                % (out, len(payload), len(blob), crc32(payload)))
            messagebox.showinfo("完成", "打包成功:\n%s" % os.path.abspath(out))
        except Exception as ex:
            say("msp 失败: %s" % ex, ok=False)
            messagebox.showerror("错误", str(ex))

    ttk.Button(f2, text="打 包", command=do_msp).grid(row=5, column=1,
                                                     sticky="we", pady=12)
    f2.columnconfigure(1, weight=1)

    # ---- 页 3: store 清单 ----
    f3 = ttk.Frame(nb, padding=14)
    nb.add(f3, text=" store 清单（manifest.json） ")
    v_url = tk.StringVar()
    v_mout = tk.StringVar(value="manifest.json")
    files = []

    ttk.Label(f3, text="源地址 base_url").grid(row=0, column=0, sticky="w", pady=6)
    ttk.Entry(f3, textvariable=v_url, width=46).grid(row=0, column=1,
                                                     columnspan=2, sticky="we", padx=6)
    ttk.Label(f3, text="文件列表").grid(row=1, column=0, sticky="nw", pady=6)
    lb = tk.Listbox(f3, height=8, font=("Consolas", 9))
    lb.grid(row=1, column=1, sticky="nwe", padx=6)

    def add_files():
        for p in filedialog.askopenfilenames():
            if p not in files:
                files.append(p)
                lb.insert("end", os.path.basename(p))

    def rm_sel():
        for i in reversed(lb.curselection()):
            lb.delete(i)
            files.pop(i)

    bf3 = ttk.Frame(f3)
    bf3.grid(row=1, column=2, sticky="nw", padx=6)
    ttk.Button(bf3, text="添加", command=add_files).pack(fill="x", pady=2)
    ttk.Button(bf3, text="移除选中", command=rm_sel).pack(fill="x", pady=2)
    ttk.Button(bf3, text="清空",
               command=lambda: (lb.delete(0, "end"), files.clear())).pack(fill="x")

    ttk.Label(f3, text="清单输出").grid(row=2, column=0, sticky="w", pady=(12, 6))
    ttk.Entry(f3, textvariable=v_mout, width=46).grid(row=2, column=1,
                                                      sticky="we", padx=6)

    def do_manifest():
        try:
            entries = build_manifest(v_url.get().strip(), files)
            out = v_mout.get().strip() or "manifest.json"
            with open(out, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            for e in entries:
                say("  %s | %d B | crc=0x%08X | %s"
                    % (e["name"], e["size"], e["crc"], e.get("type", "script")))
            say("manifest ok: %s (%d 条)" % (out, len(entries)))
            messagebox.showinfo("完成", "清单已生成:\n%s" % os.path.abspath(out))
        except Exception as ex:
            say("manifest 失败: %s" % ex, ok=False)
            messagebox.showerror("错误", str(ex))

    ttk.Button(f3, text="生成清单", command=do_manifest).grid(row=3, column=1,
                                                             sticky="we", pady=10)
    f3.columnconfigure(1, weight=1)

    say("Memoria OS 打包器就绪。应用包输出到桌面（名字-版本.msp）；"
        "payload 上限 1MB（固件约束）。")
    app.mainloop()


if __name__ == "__main__":
    if sys.stdout is None:                     # PyInstaller --noconsole 无控制台
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run_gui()
