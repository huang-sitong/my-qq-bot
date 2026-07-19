import asyncio
import json

import websockets


async def main():
    uri = "ws://localhost:5600/v1/events"

    async with websockets.connect(uri) as ws:
        print(f"已连接: {uri}")

        # 发送鉴权包
        auth = {"op": 3}
        await ws.send(json.dumps(auth))
        print(f"发送鉴权: {json.dumps(auth)}")

        # 持续接收事件推送
        async for message in ws:
            data = json.loads(message)
            print(f"[事件] {json.dumps(data, ensure_ascii=False, indent=2)}")


asyncio.run(main())
