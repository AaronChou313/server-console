"""Manage the independent Mihomo engine even when the web service is down."""
import argparse
import asyncio
import json
import sys

from fastapi import HTTPException

from . import mihomo_broker as broker


async def main():
    parser = argparse.ArgumentParser(description="管理独立 Mihomo 服务")
    parser.add_argument("command", choices=["status", "start", "stop", "restart", "rules", "logs"])
    args = parser.parse_args()
    try:
        if args.command == "logs":
            print((await broker.request("GET", "/engine/logs"))["text"])
            return
        if args.command == "rules":
            data = await broker.request("GET", "/rules")
        elif args.command == "status":
            data = await broker.state()
        else:
            data = await broker.change({"start": "enable", "stop": "disable", "restart": "restart"}[args.command])
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except HTTPException as error:
        sys.exit(error.detail)


if __name__ == "__main__":
    asyncio.run(main())
