# qq_files.py —— 群文件：看有什么、挑有用的下下来（2026-11-12）
import argparse, json, os, sys, time, urllib.request

BASE = os.environ.get("WHALE_QQ_API", "http://127.0.0.1:6700")
TOKEN = os.environ.get("WHALE_QQ_TOKEN", "qqai-dsh-http-2026")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAVE = os.path.join(ROOT, "qq_state", "files")


def api(name, body=None, timeout=25):
    req = urllib.request.Request(BASE + "/" + name,
                                data=json.dumps(body or {}).encode(),
                                headers={"Authorization": "Bearer " + TOKEN,
                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ls(group, folder=""):
    if folder:
        r = api("get_group_files_by_folder", {"group_id": int(group), "folder_id": folder})
    else:
        r = api("get_group_root_files", {"group_id": int(group)})
    d = r.get("data") or {}
    files = d.get("files") or []
    dirs = d.get("folders") or []
    print("文件夹：")
    for x in dirs:
        print("  [%s] %s" % (x.get("folder_id"), x.get("folder_name")))
    print("文件：")
    for f in files:
        print("  %-40s %8sKB  %s  上传者:%s" % (
            str(f.get("file_name"))[:40],
            round(int(f.get("file_size") or 0) / 1024, 1),
            time.strftime("%m-%d", time.localtime(int(f.get("upload_time") or 0))),
            f.get("uploader_name")))
    return files


def grab(group, file_id, name):
    r = api("get_group_file_url", {"group_id": int(group), "file_id": file_id})
    url = (r.get("data") or {}).get("url")
    if not url:
        return None, "拿不到下载地址：%s" % str(r)[:120]
    os.makedirs(SAVE, exist_ok=True)
    safe = "".join(c for c in str(name) if c not in '\\/:*?"<>|')[:120] or ("file-%d" % int(time.time()))
    out = os.path.join(SAVE, safe)
    try:
        urllib.request.urlretrieve(url, out)
        return out, "ok"
    except Exception as e:
        return None, "下载失败：%s" % e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", type=int, required=True)
    ap.add_argument("--folder", default="")
    ap.add_argument("--get", default="", help="文件名包含这个字符串就下")
    a = ap.parse_args()
    files = ls(a.group, a.folder)
    if a.get:
        hit = [f for f in files if a.get in str(f.get("file_name"))]
        if not hit:
            print("没有匹配的文件")
            return 1
        for f in hit:
            p, why = grab(a.group, f.get("file_id"), f.get("file_name"))
            print(("下好：" + p) if p else why)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
