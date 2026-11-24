"""在有限时长的子进程中访问外部数据源。"""

import json
import sys
from pathlib import Path

import galaxy
import public


def main():
    task = json.load(sys.stdin)
    try:
        match task["action"]:
            case "load":
                galaxy.sdk()
                result = True
            case "check":
                result = galaxy.read(task["connection"], check_only=True)
            case "quotes" | "close" | "calendar":
                result = galaxy.market(task["connection"], task["action"], task.get("symbols", []), task.get("session_date"), sys.argv[1])
            case "fetch":
                result = (galaxy.read(task["connection"], previous_stocks=task["previous_stocks"]) if task["source"] == "galaxy"
                          else public.fetch_catalog()[0])
        response = {"data": result}
    except galaxy.SourceError as exc:
        response = {"code": exc.code, "error": str(exc)}
    except Exception:
        # SDK 的异常文本及原生库输出可能含有连接信息。
        response = {"code": "source_failed", "error": "数据源请求失败，请检查网络和证券数据权限后重试。"}
    Path(sys.argv[1]).write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
